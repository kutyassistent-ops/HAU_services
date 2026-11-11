-- Схема базы данных «ЖКУ-Учёт»
-- СУБД: SQLite 3.35+ (поддерживается ON CONFLICT DO UPDATE и RETURNING)
-- Все даты хранятся в формате ISO 8601: YYYY-MM-DD, дата-время — YYYY-MM-DD HH:MM:SS.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT    NOT NULL,
    role          TEXT    NOT NULL CHECK (role IN ('owner', 'tenant')),
    full_name     TEXT    NOT NULL DEFAULT '',
    email         TEXT,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS properties (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id    INTEGER NOT NULL,
    address     TEXT    NOT NULL,
    invite_code TEXT    NOT NULL UNIQUE,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (owner_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS property_tariffs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id    INTEGER NOT NULL,
    effective_from TEXT    NOT NULL,
    tariff_el      REAL    NOT NULL CHECK (tariff_el    >= 0),
    tariff_water   REAL    NOT NULL CHECK (tariff_water >= 0),
    tariff_com1    REAL    NOT NULL CHECK (tariff_com1  >= 0),
    tariff_com2    REAL    NOT NULL CHECK (tariff_com2  >= 0),
    fixed_fee      REAL    NOT NULL CHECK (fixed_fee    >= 0),
    created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (property_id, effective_from),
    FOREIGN KEY (property_id) REFERENCES properties(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS tenant_properties (
    tenant_id   INTEGER NOT NULL,
    property_id INTEGER NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (tenant_id, property_id),
    FOREIGN KEY (tenant_id)   REFERENCES users(id)      ON DELETE CASCADE,
    FOREIGN KEY (property_id) REFERENCES properties(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS meter_readings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id  INTEGER NOT NULL,
    user_id      INTEGER NOT NULL,
    reading_date TEXT    NOT NULL,
    hot_water    REAL    NOT NULL CHECK (hot_water    >= 0),
    cool_water   REAL    NOT NULL CHECK (cool_water   >= 0),
    electricity  REAL    NOT NULL CHECK (electricity  >= 0),
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (property_id, reading_date),
    FOREIGN KEY (property_id) REFERENCES properties(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id)     REFERENCES users(id)      ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS invoices (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id INTEGER NOT NULL,
    reading_id  INTEGER NOT NULL UNIQUE,
    tariff_id   INTEGER,
    total       REAL    NOT NULL CHECK (total >= 0),
    details     TEXT    NOT NULL,
    paid        INTEGER NOT NULL DEFAULT 0 CHECK (paid IN (0, 1)),
    paid_at     TEXT,
    paid_by     INTEGER,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (property_id) REFERENCES properties(id) ON DELETE CASCADE,
    FOREIGN KEY (reading_id)  REFERENCES meter_readings(id) ON DELETE CASCADE,
    FOREIGN KEY (tariff_id)   REFERENCES property_tariffs(id) ON DELETE SET NULL,
    FOREIGN KEY (paid_by)     REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER,
    action     TEXT    NOT NULL,
    entity     TEXT    NOT NULL,
    entity_id  INTEGER,
    details    TEXT,
    created_at TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_properties_owner
    ON properties (owner_id);

CREATE INDEX IF NOT EXISTS idx_tariffs_property_date
    ON property_tariffs (property_id, effective_from DESC);

CREATE INDEX IF NOT EXISTS idx_readings_property_date
    ON meter_readings (property_id, reading_date DESC);

CREATE INDEX IF NOT EXISTS idx_invoices_property_paid
    ON invoices (property_id, paid);

CREATE INDEX IF NOT EXISTS idx_tenant_properties_tenant
    ON tenant_properties (tenant_id);
