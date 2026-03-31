#!/usr/bin/env python3
"""
EthMBus TCP/IP Poller
Connects to EthMBus-XL in TCP/IP transparent-server mode, sends M-Bus REQ_UD2
frames for each configured primary address, parses the raw RSP_UD response,
and stores the data in PostgreSQL.

Config (environment variables):
    MBUS_IP         IP address of EthMBus-XL converter              (required)
    MBUS_PORT       TCP port of converter                            (default: 9999)
    MBUS_ADDRESSES  Comma-separated M-Bus primary addresses to poll  (default: 1)
    MBUS_POLL       Poll interval in seconds                         (default: 5)
    MBUS_TIMEOUT    TCP socket timeout in seconds                    (default: 5)
    DB_DSN          libpq connection string                          (required)
    LOG_LEVEL       DEBUG / INFO / WARNING                           (default: INFO)
"""

import logging
import os
import signal
import socket
import sys
import time
from datetime import datetime, timezone

import meterbus
import psycopg2
import psycopg2.extras

# ── Configuration ─────────────────────────────────────────────────────────────

CONVERTER_IP   = os.environ["MBUS_IP"]
CONVERTER_PORT = int(os.getenv("MBUS_PORT",      "9999"))
POLL_INTERVAL  = int(os.getenv("MBUS_POLL",      "5"))
TCP_TIMEOUT    = float(os.getenv("MBUS_TIMEOUT", "5"))
ADDRESSES      = [
    int(a.strip())
    for a in os.getenv("MBUS_ADDRESSES", "1").split(",")
    if a.strip()
]
DB_DSN    = os.environ["DB_DSN"]
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("mbus_poller")

# ── Graceful shutdown ─────────────────────────────────────────────────────────

_running = True


def _stop(signum, frame):  # noqa: ARG001
    global _running
    log.info("Signal received – shutting down…")
    _running = False


signal.signal(signal.SIGINT,  _stop)
signal.signal(signal.SIGTERM, _stop)

# ── M-Bus medium lookup ───────────────────────────────────────────────────────

MEDIUM_NAMES = {
    0x00: "Other",           0x01: "Oil",            0x02: "Electricity",
    0x03: "Gas",             0x04: "Heat (outlet)",  0x05: "Steam",
    0x06: "Hot Water",       0x07: "Water",          0x08: "Heat Cost Allocator",
    0x09: "Compressed Air",  0x0A: "Cooling (outlet)", 0x0B: "Cooling (inlet)",
    0x0C: "Heat (inlet)",    0x0D: "Heat/Cooling",   0x0E: "Bus/System",
    0x0F: "Unknown",         0x20: "Breaker",        0x21: "Valve",
    0x24: "Waste Water",     0x25: "Garbage",        0xFF: "Radio Converter",
}

# ── DB connection with retry ──────────────────────────────────────────────────


def connect_with_retry(dsn: str, retries: int = 30, delay: float = 2.0):
    """Try to connect to PostgreSQL, retrying until the DB is ready."""
    for attempt in range(1, retries + 1):
        try:
            conn = psycopg2.connect(dsn)
            conn.autocommit = False
            log.info("Connected to PostgreSQL (attempt %d)", attempt)
            return conn
        except psycopg2.OperationalError as exc:
            log.warning("DB not ready yet (attempt %d/%d): %s", attempt, retries, exc)
            if attempt == retries:
                log.error("Giving up – could not connect to database.")
                sys.exit(1)
            time.sleep(delay)

# ── M-Bus frame helpers ───────────────────────────────────────────────────────


def build_req_ud2(address: int) -> bytes:
    """Build M-Bus REQ_UD2 short frame: 10 5B <A> <CS> 16"""
    c  = 0x5B
    a  = address & 0xFF
    cs = (c + a) % 256
    return bytes([0x10, c, a, cs, 0x16])


def recv_exactly(sock: socket.socket, n: int) -> bytes:
    """Read exactly n bytes from socket, raising ConnectionError if the socket closes."""
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Connection closed by device")
        buf.extend(chunk)
    return bytes(buf)


def recv_mbus_frame(sock: socket.socket) -> bytes:
    """
    Read one complete M-Bus frame from the socket.
    Skips any leading garbage bytes (banners, etc.); expects a long frame (0x68) or ACK (0xE5).

    Long frame layout:
        0x68 | L | L | 0x68 | C | A | CI | [data: L-3 bytes] | CS | 0x16
        Total length = L + 6 bytes
    """
    while True:
        # Scan for frame start byte
        b = 0
        while True:
            first = recv_exactly(sock, 1)
            b = first[0]
            if b == 0xE5:
                return bytes([0xE5])
            if b == 0x68:
                break  # Potential frame start
            # Otherwise skip this byte (banner, noise, etc.)

        # Read both length bytes
        ll = recv_exactly(sock, 2)
        length_1 = ll[0]
        length_2 = ll[1]

        # Valid M-Bus: both length bytes must match and be reasonable (3-250)
        if length_1 != length_2 or length_1 < 3 or length_1 > 250:
            log.debug(
                "Invalid length bytes 0x%02X vs 0x%02X (banner/garbage), continuing scan...",
                length_1, length_2,
            )
            continue  # Try finding next 0x68

        length = length_1

        # Read: second start (0x68) + L data bytes + CS + stop (0x16) = length + 3 bytes
        rest = recv_exactly(sock, length + 3)
        frame = bytes([0x68]) + ll + rest

        # Validate frame structure
        if frame[3] != 0x68:
            log.debug("Invalid second start byte 0x%02X, retrying...", frame[3])
            continue  # Try finding next 0x68

        if frame[-1] != 0x16:
            log.debug("Invalid stop byte 0x%02X, retrying...", frame[-1])
            continue  # Try finding next 0x68

        # Valid frame!
        return frame


def drain_initial_banner(sock: socket.socket) -> None:
    """
    The EthMBus-XL sends a greeting banner on connect.
    Drain it completely so subsequent reads get actual M-Bus frames.
    Banner ends with NUL bytes or a timeout.
    """
    old_timeout = sock.gettimeout()
    sock.settimeout(0.5)
    drained = bytearray()
    try:
        while True:
            try:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                drained.extend(chunk)
                # Banner typically ends with \r\0 or multiple NUL bytes
                if drained.endswith(b'\x00') or len(drained) > 500:
                    break
            except socket.timeout:
                break
    finally:
        sock.settimeout(old_timeout)
    if drained:
        log.debug("Drained banner (%d bytes)", len(drained))


def get_converter_info() -> dict:
    """Get converter metadata from configuration (IP + port)."""
    return {
        "name": f"EthMBus-XL @ {CONVERTER_IP}:{CONVERTER_PORT}",
        "mac": "00:00:00:00:00:00",  # Unknown in transparent mode
    }


def _decode_manufacturer(raw: bytes) -> str:
    """Decode 2-byte little-endian M-Bus manufacturer code to 3-char string."""
    try:
        w  = int.from_bytes(raw, "little")
        c1 = chr(((w >> 10) & 0x1F) + ord("A") - 1)
        c2 = chr(((w >>  5) & 0x1F) + ord("A") - 1)
        c3 = chr(( w        & 0x1F) + ord("A") - 1)
        return c1 + c2 + c3
    except Exception:
        return "???"


def _decode_bcd(data: bytes) -> int:
    """Decode 4-byte BCD identification number (LSB first) to integer."""
    return int("".join(f"{b:02X}" for b in reversed(data)))


def parse_fixed_header(frame: bytes) -> dict:
    """
    Parse the 12-byte fixed header from a CI=0x72 variable-data frame.
    Frame layout: 68 L L 68 C A 0x72 [12-byte header] [records…] CS 16
                  ^0                  ^7
    Header fields (bytes 7–18):
        [0:4]  Identification number (BCD, LSB first)
        [4:6]  Manufacturer (2-byte encoded)
        [6]    Version
        [7]    Medium
        [8]    Access counter
        [9]    Status
        [10:12] Signature (usually 0x0000)
    """
    if len(frame) < 19 or frame[6] != 0x72:
        return {}
    h = frame[7:19]
    try:
        medium = h[7]
        return {
            "serial":       _decode_bcd(h[0:4]),
            "manufacturer": _decode_manufacturer(h[4:6]),
            "version":      h[6],
            "medium":       medium,
            "medium_s":     MEDIUM_NAMES.get(medium, f"0x{medium:02X}"),
            "status":       h[9],
        }
    except Exception as exc:
        log.debug("Fixed-header parse error: %s", exc)
        return {}

# ── DB operations ─────────────────────────────────────────────────────────────


def upsert_converter(cur, ip: str, info: dict) -> int:
    cur.execute(
        """
        INSERT INTO converters (name, ip, mac)
        VALUES (%s, %s, %s)
        ON CONFLICT (ip) DO UPDATE
            SET name = EXCLUDED.name,
                mac  = COALESCE(
                           NULLIF(EXCLUDED.mac, '00:00:00:00:00:00'),
                           converters.mac
                       )
        RETURNING id
        """,
        (info.get("name", ""), ip, info.get("mac", "00:00:00:00:00:00")),
    )
    return cur.fetchone()[0]


def upsert_meter(cur, converter_id: int, seq_id: int, address: int, hdr: dict) -> int:
    cur.execute(
        """
        INSERT INTO meters
            (converter_id, meter_seq_id, mbus_address,
             serial_number, meter_type, meter_type_s, manufacturer, meter_version)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (converter_id, meter_seq_id) DO UPDATE
            SET mbus_address  = EXCLUDED.mbus_address,
                serial_number = COALESCE(EXCLUDED.serial_number, meters.serial_number),
                meter_type    = COALESCE(EXCLUDED.meter_type,    meters.meter_type),
                meter_type_s  = COALESCE(EXCLUDED.meter_type_s,  meters.meter_type_s),
                manufacturer  = COALESCE(EXCLUDED.manufacturer,  meters.manufacturer),
                meter_version = COALESCE(EXCLUDED.meter_version, meters.meter_version)
        RETURNING id
        """,
        (
            converter_id,
            seq_id,
            address,
            hdr.get("serial"),
            hdr.get("medium"),
            hdr.get("medium_s"),
            hdr.get("manufacturer"),
            hdr.get("version"),
        ),
    )
    return cur.fetchone()[0]


def insert_readout(cur, meter_id: int, success: bool, hdr: dict) -> int:
    # status=0 → ok, status=1 → no response / parse error
    # ok_count / err_count not available in TCP transparent mode
    cur.execute(
        """
        INSERT INTO meter_readouts
            (meter_id, polled_at, status, ok_count, err_count, meter_status)
        VALUES (%s, %s, %s, NULL, NULL, %s)
        RETURNING id
        """,
        (
            meter_id,
            datetime.now(tz=timezone.utc),
            0 if success else 1,
            hdr.get("status"),
        ),
    )
    return cur.fetchone()[0]


def insert_values(cur, readout_id: int, telegram) -> int:
    rows = []
    for seq_id, record in enumerate(telegram.records):
        try:
            raw_val  = record.parsed_value
            val_text = str(raw_val) if raw_val is not None else None
            val_num  = None
            if raw_val is not None:
                try:
                    val_num = float(raw_val)
                except (TypeError, ValueError):
                    pass

            name = unit = None
            try:
                name = record.value_information.description
            except AttributeError:
                pass
            try:
                unit = record.unit
            except AttributeError:
                pass

            storage = getattr(record, "storage_number", 0) or 0
            tariff  = getattr(record, "tariff",         0) or 0

            rows.append((
                readout_id, seq_id, name, val_text, val_num,
                unit, storage, tariff, 0, None, None, None,
            ))
        except Exception as exc:
            log.debug("Skipping record %d: %s", seq_id, exc)

    if rows:
        psycopg2.extras.execute_values(
            cur,
            """
            INSERT INTO meter_values
                (readout_id, val_seq_id, name, value_text, value_numeric,
                 units_s, storage, tariff, sub_unit,
                 data_type, value_type, error_string)
            VALUES %s
            """,
            rows,
        )
    return len(rows)

# ── Poll cycle ────────────────────────────────────────────────────────────────


def poll_once(conn) -> None:
    log.debug("Connecting to %s:%d", CONVERTER_IP, CONVERTER_PORT)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(TCP_TIMEOUT)
            sock.connect((CONVERTER_IP, CONVERTER_PORT))

            # Drain the converter's initial greeting banner
            drain_initial_banner(sock)

            # Get converter metadata
            converter_info = get_converter_info()

            for seq_id, address in enumerate(ADDRESSES):
                frame_bytes = None
                hdr         = {}
                telegram    = None
                success     = False

                try:
                    req = build_req_ud2(address)
                    log.debug("REQ_UD2 → addr %d: %s", address, req.hex().upper())
                    sock.sendall(req)

                    frame_bytes = recv_mbus_frame(sock)
                    log.debug(
                        "RSP_UD ← addr %d (%d B): %s",
                        address, len(frame_bytes), frame_bytes.hex().upper(),
                    )

                    if frame_bytes == bytes([0xE5]):
                        log.info("Address %d: ACK only (E5) – no user data", address)
                        success = True
                    else:
                        hdr      = parse_fixed_header(frame_bytes)
                        telegram = meterbus.load(frame_bytes)
                        success  = True

                except socket.timeout:
                    log.warning("Address %d: no response within %.1fs", address, TCP_TIMEOUT)

                except (ConnectionError, ValueError) as exc:
                    log.warning("Address %d: frame error – %s", address, exc)
                    break  # socket may be dead; skip remaining addresses this cycle

                except Exception as exc:
                    log.warning("Address %d: unexpected error – %s", address, exc)

                # Persist whatever we managed to collect
                try:
                    with conn:
                        with conn.cursor() as cur:
                            conv_id    = upsert_converter(cur, CONVERTER_IP, converter_info)
                            meter_id   = upsert_meter(cur, conv_id, seq_id, address, hdr)
                            readout_id = insert_readout(cur, meter_id, success, hdr)
                            n_vals     = insert_values(cur, readout_id, telegram) if telegram else 0
                    log.info(
                        "Address %d: %d value(s) stored%s",
                        address, n_vals, "" if success else " [poll failed]",
                    )
                except psycopg2.Error as exc:
                    log.error("DB error for address %d: %s", address, exc)
                    conn.rollback()

    except (OSError, socket.timeout) as exc:
        log.warning("TCP connection failed: %s", exc)

# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> None:
    log.info(
        "EthMBus TCP poller starting – converter=%s:%d  addresses=%s  interval=%ds",
        CONVERTER_IP, CONVERTER_PORT, ADDRESSES, POLL_INTERVAL,
    )
    conn = connect_with_retry(DB_DSN)

    next_poll = time.monotonic()
    while _running:
        now = time.monotonic()
        if now >= next_poll:
            poll_once(conn)
            next_poll = now + POLL_INTERVAL
        time.sleep(0.1)

    conn.close()
    log.info("Poller stopped.")


if __name__ == "__main__":
    main()
