"""Простые миграции схемы SQLite.

Схема в ``schema.sql`` описывает актуальное состояние для новой базы.
Для уже существующих баз применяются перечисленные ниже миграции; каждая
миграция идемпотентна и фиксируется в таблице ``schema_migrations``.
"""
from __future__ import annotations

from extensions import get_db


def _columns(table: str) -> set[str]:
    return {row["name"] for row in get_db().execute(f"PRAGMA table_info({table})").fetchall()}


def _migration_001_properties_archived_at(db) -> None:
    """Добавляет мягкое архивирование объектов."""
    if "archived_at" not in _columns("properties"):
        db.execute("ALTER TABLE properties ADD COLUMN archived_at TEXT")


def _migration_002_payments(db) -> None:
    """Добавляет таблицу частичных платежей по счетам."""
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS payments (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_id INTEGER NOT NULL,
            amount     REAL    NOT NULL CHECK (amount > 0),
            paid_at    TEXT    NOT NULL DEFAULT (datetime('now')),
            created_by INTEGER,
            comment    TEXT,
            FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE CASCADE,
            FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
        );

        CREATE INDEX IF NOT EXISTS idx_payments_invoice
            ON payments (invoice_id, paid_at);
        """
    )


def _migration_003_indexes(db) -> None:
    """Добавляет индексы для истории, платежей и журнала действий."""
    db.execute("CREATE INDEX IF NOT EXISTS idx_invoices_reading ON invoices (reading_id)")
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_user_created "
        "ON audit_log (user_id, created_at DESC)"
    )


def _migration_004_electricity_zones(db) -> None:
    """Добавляет дневной/ночной тариф и показания по зонам."""
    tariff_columns = _columns("property_tariffs")
    if "tariff_el_day" not in tariff_columns:
        db.execute("ALTER TABLE property_tariffs ADD COLUMN tariff_el_day REAL")
    if "tariff_el_night" not in tariff_columns:
        db.execute("ALTER TABLE property_tariffs ADD COLUMN tariff_el_night REAL")
    reading_columns = _columns("meter_readings")
    if "electricity_day" not in reading_columns:
        db.execute("ALTER TABLE meter_readings ADD COLUMN electricity_day REAL")
    if "electricity_night" not in reading_columns:
        db.execute("ALTER TABLE meter_readings ADD COLUMN electricity_night REAL")


def _migration_005_tenant_shares(db) -> None:
    """Добавляет долю арендатора в счёте."""
    if "share" in _columns("tenant_properties"):
        return
    db.execute(
        "ALTER TABLE tenant_properties "
        "ADD COLUMN share REAL NOT NULL DEFAULT 1.0 CHECK (share > 0 AND share <= 1)"
    )
    db.execute(
        """
        UPDATE tenant_properties
        SET share = 1.0 / (
            SELECT COUNT(*) FROM tenant_properties tp2
            WHERE tp2.property_id = tenant_properties.property_id
        )
        """
    )


def _migration_006_api_tokens(db) -> None:
    """Добавляет токены для read-only REST API."""
    if "api_token" not in _columns("users"):
        db.execute("ALTER TABLE users ADD COLUMN api_token TEXT")
    db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_api_token ON users (api_token)"
    )


MIGRATIONS = (
    ("001_properties_archived_at", _migration_001_properties_archived_at),
    ("002_payments", _migration_002_payments),
    ("003_indexes", _migration_003_indexes),
    ("004_electricity_zones", _migration_004_electricity_zones),
    ("005_tenant_shares", _migration_005_tenant_shares),
    ("006_api_tokens", _migration_006_api_tokens),
)


def apply_migrations() -> list[str]:
    """Применяет неприменённые миграции и возвращает их список."""
    db = get_db()
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version    TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    applied = {
        row["version"]
        for row in db.execute("SELECT version FROM schema_migrations").fetchall()
    }
    executed: list[str] = []
    for version, migration in MIGRATIONS:
        if version in applied:
            continue
        migration(db)
        db.execute("INSERT INTO schema_migrations (version) VALUES (?)", (version,))
        executed.append(version)
    db.commit()
    return executed
