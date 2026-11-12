"""Тесты миграций существующей базы данных."""
from __future__ import annotations

import sqlite3

from app import create_app
from extensions import get_db
from migrations import apply_migrations

OLD_SCHEMA = """
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL,
    full_name TEXT NOT NULL DEFAULT '',
    email TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE properties (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER NOT NULL,
    address TEXT NOT NULL,
    invite_code TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE property_tariffs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id INTEGER NOT NULL,
    effective_from TEXT NOT NULL,
    tariff_el REAL NOT NULL,
    tariff_water REAL NOT NULL,
    tariff_com1 REAL NOT NULL,
    tariff_com2 REAL NOT NULL,
    fixed_fee REAL NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (property_id, effective_from)
);
CREATE TABLE tenant_properties (
    tenant_id INTEGER NOT NULL,
    property_id INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (tenant_id, property_id)
);
CREATE TABLE meter_readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    reading_date TEXT NOT NULL,
    hot_water REAL NOT NULL,
    cool_water REAL NOT NULL,
    electricity REAL NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (property_id, reading_date)
);
CREATE TABLE invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id INTEGER NOT NULL,
    reading_id INTEGER NOT NULL UNIQUE,
    tariff_id INTEGER,
    total REAL NOT NULL,
    details TEXT NOT NULL,
    paid INTEGER NOT NULL DEFAULT 0,
    paid_at TEXT,
    paid_by INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    action TEXT NOT NULL,
    entity TEXT NOT NULL,
    entity_id INTEGER,
    details TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
INSERT INTO users (username, password_hash, role, full_name)
VALUES ('old_owner', 'x', 'owner', 'Старый владелец');
INSERT INTO users (username, password_hash, role, full_name)
VALUES ('old_tenant1', 'x', 'tenant', 'Арендатор 1');
INSERT INTO users (username, password_hash, role, full_name)
VALUES ('old_tenant2', 'x', 'tenant', 'Арендатор 2');
INSERT INTO properties (owner_id, address, invite_code)
VALUES (1, 'Старый адрес', 'OLDCODE1');
INSERT INTO tenant_properties (tenant_id, property_id) VALUES (2, 1);
INSERT INTO tenant_properties (tenant_id, property_id) VALUES (3, 1);
"""


def _create_old_database(path):
    connection = sqlite3.connect(path)
    try:
        connection.executescript(OLD_SCHEMA)
        connection.commit()
    finally:
        connection.close()


def _columns(db, table):
    return {row["name"] for row in db.execute(f"PRAGMA table_info({table})").fetchall()}


def test_migrations_upgrade_old_database(tmp_path):
    db_path = tmp_path / "old.db"
    _create_old_database(db_path)
    app = create_app({
        "TESTING": True,
        "DATABASE": str(db_path),
        "SECRET_KEY": "test-secret",
        "LOG_FILE": str(tmp_path / "old.log"),
    })
    with app.app_context():
        db = get_db()
        assert "archived_at" in _columns(db, "properties")
        assert "tariff_el_day" in _columns(db, "property_tariffs")
        assert "tariff_el_night" in _columns(db, "property_tariffs")
        assert "electricity_day" in _columns(db, "meter_readings")
        assert "electricity_night" in _columns(db, "meter_readings")
        assert "share" in _columns(db, "tenant_properties")
        assert "api_token" in _columns(db, "users")
        assert db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'payments'"
        ).fetchone() is not None

        versions = {
            row["version"]
            for row in db.execute("SELECT version FROM schema_migrations").fetchall()
        }
        assert versions == {
            "001_properties_archived_at",
            "002_payments",
            "003_indexes",
            "004_electricity_zones",
            "005_tenant_shares",
            "006_api_tokens",
        }
        shares = [
            row["share"]
            for row in db.execute(
                "SELECT share FROM tenant_properties WHERE property_id = 1 ORDER BY tenant_id"
            ).fetchall()
        ]
        assert shares == [0.5, 0.5]

        # Повторный запуск миграций ничего не делает.
        assert apply_migrations() == []


def test_migrations_are_idempotent_on_current_schema(app):
    with app.app_context():
        assert apply_migrations() == []
