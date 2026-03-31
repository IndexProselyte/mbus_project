-- ============================================================
-- EthMBus SMART Converter – PostgreSQL Schema
-- Auto-applied by Docker on first container start
-- ============================================================

-- Converter info (one row per physical device)
CREATE TABLE IF NOT EXISTS converters (
    id          SERIAL      PRIMARY KEY,
    name        TEXT        NOT NULL,
    ip          INET        NOT NULL UNIQUE,
    mac         MACADDR     NOT NULL DEFAULT '00:00:00:00:00:00',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- M-Bus meters registered on a converter
CREATE TABLE IF NOT EXISTS meters (
    id              SERIAL      PRIMARY KEY,
    converter_id    INTEGER     NOT NULL REFERENCES converters(id) ON DELETE CASCADE,
    meter_seq_id    SMALLINT    NOT NULL,
    mbus_address    SMALLINT    NOT NULL,
    serial_number   BIGINT,
    meter_type      SMALLINT,
    meter_type_s    TEXT,
    manufacturer    CHAR(3),
    meter_version   SMALLINT,
    UNIQUE (converter_id, meter_seq_id)
);

-- One readout snapshot per poll cycle per meter
CREATE TABLE IF NOT EXISTS meter_readouts (
    id              BIGSERIAL   PRIMARY KEY,
    meter_id        INTEGER     NOT NULL REFERENCES meters(id) ON DELETE CASCADE,
    polled_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status          SMALLINT    NOT NULL,
    ok_count        INTEGER,
    err_count       INTEGER,
    meter_status    SMALLINT
);

CREATE INDEX IF NOT EXISTS idx_readouts_meter_polled
    ON meter_readouts (meter_id, polled_at DESC);

-- Individual data values inside a readout
CREATE TABLE IF NOT EXISTS meter_values (
    id              BIGSERIAL   PRIMARY KEY,
    readout_id      BIGINT      NOT NULL REFERENCES meter_readouts(id) ON DELETE CASCADE,
    val_seq_id      SMALLINT    NOT NULL,
    name            TEXT,
    value_text      TEXT,
    value_numeric   DOUBLE PRECISION,
    units_s         TEXT,
    storage         INTEGER     DEFAULT 0,
    tariff          INTEGER     DEFAULT 0,
    sub_unit        INTEGER     DEFAULT 0,
    data_type       SMALLINT,
    value_type      TEXT,
    error_string    TEXT
);

CREATE INDEX IF NOT EXISTS idx_values_readout
    ON meter_values (readout_id);

CREATE INDEX IF NOT EXISTS idx_values_name_polled
    ON meter_values (name, readout_id DESC);

-- ============================================================
-- View: latest value per meter per value-name / storage / tariff
-- ============================================================
CREATE OR REPLACE VIEW latest_meter_values AS
SELECT DISTINCT ON (m.id, mv.name, mv.storage, mv.tariff)
    c.name              AS converter_name,
    c.ip::text          AS converter_ip,
    m.meter_seq_id,
    m.mbus_address,
    m.serial_number,
    m.meter_type_s,
    m.manufacturer,
    mr.polled_at,
    mr.status           AS comm_status,
    mv.name             AS value_name,
    mv.value_numeric,
    mv.value_text,
    mv.units_s,
    mv.storage,
    mv.tariff,
    mv.value_type
FROM meter_values   mv
JOIN meter_readouts mr ON mr.id = mv.readout_id
JOIN meters          m ON m.id  = mr.meter_id
JOIN converters      c ON c.id  = m.converter_id
ORDER BY m.id, mv.name, mv.storage, mv.tariff, mr.polled_at DESC;
