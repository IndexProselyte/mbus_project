"""
EthMBus – REST API
Provides read access to the M-Bus data stored by the poller.
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import psycopg2
import psycopg2.extras
import os

# ── App setup ─────────────────────────────────────────────────────────────────

app = FastAPI(
    title="EthMBus API",
    description="REST API for EthMBus SMART converter data stored in PostgreSQL.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_DSN = os.environ["DB_DSN"]

# ── DB dependency ─────────────────────────────────────────────────────────────

def get_conn():
    conn = psycopg2.connect(DB_DSN, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        yield conn
    finally:
        conn.close()

# ── Pydantic models ───────────────────────────────────────────────────────────

class Converter(BaseModel):
    id: int
    name: str
    ip: str
    mac: str
    created_at: datetime

class Meter(BaseModel):
    id: int
    converter_id: int
    meter_seq_id: int
    mbus_address: int
    serial_number: Optional[int]
    meter_type: Optional[int]
    meter_type_s: Optional[str]
    manufacturer: Optional[str]
    meter_version: Optional[int]

class Readout(BaseModel):
    id: int
    meter_id: int
    polled_at: datetime
    status: int
    ok_count: Optional[int]
    err_count: Optional[int]
    meter_status: Optional[int]

class MeterValue(BaseModel):
    id: int
    readout_id: int
    val_seq_id: int
    name: Optional[str]
    value_text: Optional[str]
    value_numeric: Optional[float]
    units_s: Optional[str]
    storage: int
    tariff: int
    sub_unit: int
    data_type: Optional[int]
    value_type: Optional[str]
    error_string: Optional[str]

class LatestValue(BaseModel):
    converter_name: str
    converter_ip: str
    meter_seq_id: int
    mbus_address: int
    serial_number: Optional[int]
    meter_type_s: Optional[str]
    manufacturer: Optional[str]
    polled_at: datetime
    comm_status: int
    value_name: Optional[str]
    value_numeric: Optional[float]
    value_text: Optional[str]
    units_s: Optional[str]
    storage: int
    tariff: int
    value_type: Optional[str]

class ReadoutWithValues(BaseModel):
    readout: Readout
    values: list[MeterValue]

class ConverterStats(BaseModel):
    converter_id: int
    converter_name: str
    converter_ip: str
    total_meters: int
    total_readouts: int
    total_values: int
    last_poll: Optional[datetime]

# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", tags=["status"])
def root():
    return {"status": "ok", "service": "EthMBus API"}


@app.get("/health", tags=["status"])
def health(conn=Depends(get_conn)):
    with conn.cursor() as cur:
        cur.execute("SELECT 1")
    return {"status": "ok", "db": "connected"}


# ── Converters ────────────────────────────────────────────────────────────────

@app.get("/converters", response_model=list[Converter], tags=["converters"])
def list_converters(conn=Depends(get_conn)):
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM converters ORDER BY id")
        return cur.fetchall()


@app.get("/converters/{converter_id}", response_model=Converter, tags=["converters"])
def get_converter(converter_id: int, conn=Depends(get_conn)):
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM converters WHERE id = %s", (converter_id,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(404, "Converter not found")
    return row


@app.get("/converters/{converter_id}/stats", response_model=ConverterStats, tags=["converters"])
def converter_stats(converter_id: int, conn=Depends(get_conn)):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                c.id            AS converter_id,
                c.name          AS converter_name,
                c.ip::text      AS converter_ip,
                COUNT(DISTINCT m.id)  AS total_meters,
                COUNT(DISTINCT mr.id) AS total_readouts,
                COUNT(mv.id)          AS total_values,
                MAX(mr.polled_at)     AS last_poll
            FROM converters c
            LEFT JOIN meters        m  ON m.converter_id  = c.id
            LEFT JOIN meter_readouts mr ON mr.meter_id    = m.id
            LEFT JOIN meter_values   mv ON mv.readout_id  = mr.id
            WHERE c.id = %s
            GROUP BY c.id, c.name, c.ip
            """,
            (converter_id,),
        )
        row = cur.fetchone()
    if not row:
        raise HTTPException(404, "Converter not found")
    return row


# ── Meters ────────────────────────────────────────────────────────────────────

@app.get("/converters/{converter_id}/meters", response_model=list[Meter], tags=["meters"])
def list_meters(converter_id: int, conn=Depends(get_conn)):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM meters WHERE converter_id = %s ORDER BY meter_seq_id",
            (converter_id,),
        )
        return cur.fetchall()


@app.get("/meters/{meter_id}", response_model=Meter, tags=["meters"])
def get_meter(meter_id: int, conn=Depends(get_conn)):
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM meters WHERE id = %s", (meter_id,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(404, "Meter not found")
    return row


# ── Readouts ──────────────────────────────────────────────────────────────────

@app.get("/meters/{meter_id}/readouts", response_model=list[Readout], tags=["readouts"])
def list_readouts(
    meter_id: int,
    limit: int = Query(50, ge=1, le=1000),
    since: Optional[datetime] = Query(None, description="ISO-8601 timestamp filter"),
    conn=Depends(get_conn),
):
    with conn.cursor() as cur:
        if since:
            cur.execute(
                """
                SELECT * FROM meter_readouts
                WHERE meter_id = %s AND polled_at >= %s
                ORDER BY polled_at DESC LIMIT %s
                """,
                (meter_id, since, limit),
            )
        else:
            cur.execute(
                """
                SELECT * FROM meter_readouts
                WHERE meter_id = %s
                ORDER BY polled_at DESC LIMIT %s
                """,
                (meter_id, limit),
            )
        return cur.fetchall()


@app.get("/readouts/{readout_id}", response_model=ReadoutWithValues, tags=["readouts"])
def get_readout(readout_id: int, conn=Depends(get_conn)):
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM meter_readouts WHERE id = %s", (readout_id,))
        readout = cur.fetchone()
        if not readout:
            raise HTTPException(404, "Readout not found")
        cur.execute(
            "SELECT * FROM meter_values WHERE readout_id = %s ORDER BY val_seq_id",
            (readout_id,),
        )
        values = cur.fetchall()
    return {"readout": readout, "values": values}


# ── Values ────────────────────────────────────────────────────────────────────

@app.get("/meters/{meter_id}/values/latest", response_model=list[MeterValue], tags=["values"])
def latest_values_for_meter(meter_id: int, conn=Depends(get_conn)):
    """Return the full value set from the most recent readout of this meter."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT mv.*
            FROM meter_values mv
            JOIN meter_readouts mr ON mr.id = mv.readout_id
            WHERE mr.meter_id = %s
              AND mr.polled_at = (
                SELECT MAX(polled_at) FROM meter_readouts WHERE meter_id = %s
              )
            ORDER BY mv.val_seq_id
            """,
            (meter_id, meter_id),
        )
        return cur.fetchall()


@app.get("/meters/{meter_id}/values/history", response_model=list[dict], tags=["values"])
def value_history(
    meter_id: int,
    name: str = Query(..., description="Value name, e.g. 'Energy', 'Volume'"),
    storage: int = Query(0),
    tariff: int = Query(0),
    limit: int = Query(200, ge=1, le=5000),
    since: Optional[datetime] = Query(None),
    conn=Depends(get_conn),
):
    """Time-series of a single named value for a meter."""
    with conn.cursor() as cur:
        if since:
            cur.execute(
                """
                SELECT mr.polled_at, mv.value_numeric, mv.value_text, mv.units_s
                FROM meter_values mv
                JOIN meter_readouts mr ON mr.id = mv.readout_id
                WHERE mr.meter_id = %s
                  AND mv.name    = %s
                  AND mv.storage = %s
                  AND mv.tariff  = %s
                  AND mr.polled_at >= %s
                ORDER BY mr.polled_at DESC
                LIMIT %s
                """,
                (meter_id, name, storage, tariff, since, limit),
            )
        else:
            cur.execute(
                """
                SELECT mr.polled_at, mv.value_numeric, mv.value_text, mv.units_s
                FROM meter_values mv
                JOIN meter_readouts mr ON mr.id = mv.readout_id
                WHERE mr.meter_id = %s
                  AND mv.name    = %s
                  AND mv.storage = %s
                  AND mv.tariff  = %s
                ORDER BY mr.polled_at DESC
                LIMIT %s
                """,
                (meter_id, name, storage, tariff, limit),
            )
        rows = cur.fetchall()
    return [dict(r) for r in rows]


# ── Latest view ───────────────────────────────────────────────────────────────

@app.get("/latest", response_model=list[LatestValue], tags=["summary"])
def all_latest(
    converter_ip: Optional[str] = Query(None),
    meter_type_s: Optional[str] = Query(None),
    conn=Depends(get_conn),
):
    """
    Quick summary: latest value per (meter, value_name, storage, tariff).
    Backed by the latest_meter_values view.
    """
    filters = []
    params = []
    if converter_ip:
        filters.append("converter_ip = %s::inet")
        params.append(converter_ip)
    if meter_type_s:
        filters.append("meter_type_s ILIKE %s")
        params.append(f"%{meter_type_s}%")

    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    with conn.cursor() as cur:
        cur.execute(f"SELECT * FROM latest_meter_values {where} ORDER BY meter_seq_id, value_name", params)
        return cur.fetchall()
