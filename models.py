"""Доменная модель приложения «ЖКУ-Учёт».

Модуль содержит:
* класс пользователя для Flask-Login;
* работу с паролями (bcrypt);
* инициализацию и миграцию схемы БД;
* расчёт начислений и пересчёт счетов;
* выборки для дашбордов, истории и статистики.

Все денежные величины округляются до копеек по правилу ROUND_HALF_UP.
"""
from __future__ import annotations

import json
import math
import secrets
import string
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

import bcrypt
from flask import current_app
from flask_login import UserMixin

from extensions import get_db


# ---------------------------------------------------------------------------
# Пользователи и пароли
# ---------------------------------------------------------------------------
class User(UserMixin):
    """Пользователь системы. Роль: owner (арендодатель) или tenant (арендатор)."""

    def __init__(self, row: Any):
        self.id = row["id"]
        self.username = row["username"]
        self.role = row["role"]
        self.full_name = _optional_value(row, "full_name") or ""
        self.email = _optional_value(row, "email") or ""
        self.created_at = _optional_value(row, "created_at")

    @property
    def is_owner(self) -> bool:
        return self.role == "owner"

    @property
    def is_tenant(self) -> bool:
        return self.role == "tenant"

    @property
    def display_name(self) -> str:
        return self.full_name or self.username


def load_user(user_id: str | int) -> User | None:
    row = get_db().execute(
        "SELECT id, username, role, full_name, email, created_at FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    return User(row) if row else None


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def check_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def generate_api_token(user_id: int) -> str:
    """Выпускает новый токен для мобильного/внешнего клиента."""
    token = secrets.token_urlsafe(32)
    db = get_db()
    db.execute("UPDATE users SET api_token = ? WHERE id = ?", (token, user_id))
    log_audit("user.api_token", "user", user_id, None, user_id)
    return token


def revoke_api_token(user_id: int) -> None:
    db = get_db()
    db.execute("UPDATE users SET api_token = NULL WHERE id = ?", (user_id,))
    log_audit("user.api_token_revoke", "user", user_id, None, user_id)


def load_user_by_api_token(token: str | None) -> User | None:
    if not token:
        return None
    row = get_db().execute(
        """
        SELECT id, username, role, full_name, email, created_at
        FROM users WHERE api_token = ?
        """,
        (token,),
    ).fetchone()
    return User(row) if row else None


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------
def money(value: float | int | Decimal | str) -> float:
    """Округление денежной суммы до двух знаков."""
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def quantity(value: float | int | Decimal | str) -> float:
    """Округление показаний/расхода до трёх знаков."""
    return float(Decimal(str(value)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))


def parse_iso_date(value: str) -> date:
    """Разбор даты в формате YYYY-MM-DD с понятной ошибкой."""
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (ValueError, TypeError) as exc:
        raise ValueError("Дата должна быть в формате ГГГГ-ММ-ДД.") from exc


def today_iso() -> str:
    return date.today().isoformat()


def generate_invite_code(length: int = 8) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def unique_invite_code() -> str:
    db = get_db()
    while True:
        code = generate_invite_code()
        if not db.execute("SELECT 1 FROM properties WHERE invite_code = ?", (code,)).fetchone():
            return code


def log_audit(action: str, entity: str, entity_id: int | None = None,
              details: dict | str | None = None, user_id: int | None = None) -> None:
    if isinstance(details, dict):
        details = json.dumps(details, ensure_ascii=False)
    get_db().execute(
        "INSERT INTO audit_log (user_id, action, entity, entity_id, details) VALUES (?, ?, ?, ?, ?)",
        (user_id, action, entity, entity_id, details),
    )


# ---------------------------------------------------------------------------
# Инициализация БД
# ---------------------------------------------------------------------------
def init_db() -> None:
    db = get_db()
    schema_path = current_app.root_path + "/schema.sql"
    with open(schema_path, encoding="utf-8") as fh:
        db.executescript(fh.read())
    from migrations import apply_migrations

    apply_migrations()
    db.commit()


# ---------------------------------------------------------------------------
# Тарифы
# ---------------------------------------------------------------------------
TARIFF_FIELDS = ("tariff_el", "tariff_water", "tariff_com1", "tariff_com2", "fixed_fee")
TARIFF_ZONE_FIELDS = ("tariff_el_day", "tariff_el_night")
TARIFF_LABELS = {
    "tariff_el": "Электроэнергия",
    "tariff_water": "Вода",
    "tariff_com1": "Водоотведение холодной воды",
    "tariff_com2": "Общее водоотведение",
    "fixed_fee": "Фиксированная плата",
    "tariff_el_day": "Электроэнергия, день",
    "tariff_el_night": "Электроэнергия, ночь",
}


def _optional_value(row: Any, key: str) -> Any:
    """Безопасно читает необязательное поле из dict или sqlite3.Row."""
    if row is None:
        return None
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return None


def validate_tariff_values(values: dict[str, Any]) -> dict[str, float | None]:
    result: dict[str, float | None] = {}
    for field in TARIFF_FIELDS:
        raw = values.get(field)
        try:
            number = float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Поле «{TARIFF_LABELS[field]}» должно быть числом.") from exc
        if not math.isfinite(number):
            raise ValueError(f"Поле «{TARIFF_LABELS[field]}» содержит некорректное значение.")
        if number < 0:
            raise ValueError("Тарифы и фиксированная плата не могут быть отрицательными.")
        if number > 10_000_000:
            raise ValueError("Значение тарифа слишком велико.")
        result[field] = round(number, 4)

    zone_values: dict[str, float | None] = {}
    for field in TARIFF_ZONE_FIELDS:
        raw = values.get(field)
        if raw is None or str(raw).strip() == "":
            zone_values[field] = None
            continue
        try:
            number = float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Поле «{TARIFF_LABELS[field]}» должно быть числом.") from exc
        if not math.isfinite(number) or number < 0 or number > 10_000_000:
            raise ValueError(f"Поле «{TARIFF_LABELS[field]}» содержит некорректное значение.")
        zone_values[field] = round(number, 4)

    if (zone_values["tariff_el_day"] is None) != (zone_values["tariff_el_night"] is None):
        raise ValueError("Укажите оба тарифа на электроэнергию: день и ночь, либо оставьте оба поля пустыми.")
    result.update(zone_values)
    return result


def has_electricity_zones(tariff: dict | None) -> bool:
    if not tariff:
        return False
    return (
        _optional_value(tariff, "tariff_el_day") is not None
        and _optional_value(tariff, "tariff_el_night") is not None
    )


def add_tariff(property_id: int, effective_from: str, values: dict[str, Any],
               user_id: int | None = None) -> int:
    effective_from = parse_iso_date(effective_from).isoformat()
    data = validate_tariff_values(values)
    db = get_db()
    db.execute(
        """
        INSERT INTO property_tariffs
            (property_id, effective_from, tariff_el, tariff_water,
             tariff_com1, tariff_com2, fixed_fee,
             tariff_el_day, tariff_el_night)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(property_id, effective_from) DO UPDATE SET
            tariff_el       = excluded.tariff_el,
            tariff_water    = excluded.tariff_water,
            tariff_com1     = excluded.tariff_com1,
            tariff_com2     = excluded.tariff_com2,
            fixed_fee       = excluded.fixed_fee,
            tariff_el_day   = excluded.tariff_el_day,
            tariff_el_night = excluded.tariff_el_night
        """,
        (
            property_id,
            effective_from,
            data["tariff_el"],
            data["tariff_water"],
            data["tariff_com1"],
            data["tariff_com2"],
            data["fixed_fee"],
            data["tariff_el_day"],
            data["tariff_el_night"],
        ),
    )
    tariff_id = db.execute(
        "SELECT id FROM property_tariffs WHERE property_id = ? AND effective_from = ?",
        (property_id, effective_from),
    ).fetchone()["id"]
    log_audit("tariff.save", "property", property_id,
              {"effective_from": effective_from, **data}, user_id)
    return tariff_id


def get_tariff_for_date(property_id: int, on_date: str) -> dict | None:
    on_date = parse_iso_date(on_date).isoformat()
    row = get_db().execute(
        """
        SELECT * FROM property_tariffs
        WHERE property_id = ? AND effective_from <= ?
        ORDER BY effective_from DESC, id DESC
        LIMIT 1
        """,
        (property_id, on_date),
    ).fetchone()
    return dict(row) if row else None


def get_latest_tariff(property_id: int) -> dict | None:
    row = get_db().execute(
        """
        SELECT * FROM property_tariffs
        WHERE property_id = ?
        ORDER BY effective_from DESC, id DESC
        LIMIT 1
        """,
        (property_id,),
    ).fetchone()
    return dict(row) if row else None


def get_tariff_history(property_id: int) -> list[dict]:
    rows = get_db().execute(
        """
        SELECT * FROM property_tariffs
        WHERE property_id = ?
        ORDER BY effective_from DESC, id DESC
        """,
        (property_id,),
    ).fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Объекты недвижимости и доступ
# ---------------------------------------------------------------------------
def create_property(owner_id: int, address: str, tariff_effective_from: str,
                    tariffs: dict[str, Any]) -> int:
    address = (address or "").strip()
    if not address:
        raise ValueError("Адрес объекта обязателен.")
    if len(address) > 300:
        raise ValueError("Адрес слишком длинный (максимум 300 символов).")
    tariff_effective_from = parse_iso_date(tariff_effective_from).isoformat()
    validate_tariff_values(tariffs)

    db = get_db()
    code = unique_invite_code()
    cur = db.execute(
        "INSERT INTO properties (owner_id, address, invite_code) VALUES (?, ?, ?)",
        (owner_id, address, code),
    )
    property_id = cur.lastrowid
    add_tariff(property_id, tariff_effective_from, tariffs, owner_id)
    log_audit("property.create", "property", property_id,
              {"address": address, "invite_code": code}, owner_id)
    return property_id


def update_property_address(property_id: int, owner_id: int, address: str) -> None:
    address = (address or "").strip()
    if not address:
        raise ValueError("Адрес объекта обязателен.")
    if len(address) > 300:
        raise ValueError("Адрес слишком длинный (максимум 300 символов).")
    db = get_db()
    db.execute(
        "UPDATE properties SET address = ? WHERE id = ? AND owner_id = ?",
        (address, property_id, owner_id),
    )
    log_audit("property.update", "property", property_id, {"address": address}, owner_id)


def regenerate_invite_code(property_id: int, owner_id: int) -> str:
    code = unique_invite_code()
    db = get_db()
    db.execute(
        "UPDATE properties SET invite_code = ? WHERE id = ? AND owner_id = ?",
        (code, property_id, owner_id),
    )
    log_audit("property.invite_code", "property", property_id,
              {"invite_code": code}, owner_id)
    return code


def get_property(property_id: int) -> dict | None:
    row = get_db().execute(
        """
        SELECT p.*, u.username AS owner_username, u.full_name AS owner_name
        FROM properties p JOIN users u ON u.id = p.owner_id
        WHERE p.id = ?
        """,
        (property_id,),
    ).fetchone()
    return dict(row) if row else None


def get_user_properties(user: User, include_archived: bool = False) -> list[dict]:
    db = get_db()
    archived_filter = "" if include_archived else " AND p.archived_at IS NULL"
    if user.is_owner:
        rows = db.execute(
            f"""
            SELECT p.*, u.username AS owner_username, u.full_name AS owner_name
            FROM properties p JOIN users u ON u.id = p.owner_id
            WHERE p.owner_id = ?{archived_filter}
            ORDER BY p.address
            """,
            (user.id,),
        ).fetchall()
    else:
        rows = db.execute(
            f"""
            SELECT p.*, u.username AS owner_username, u.full_name AS owner_name
            FROM properties p
            JOIN users u ON u.id = p.owner_id
            JOIN tenant_properties tp ON tp.property_id = p.id
            WHERE tp.tenant_id = ?{archived_filter}
            ORDER BY p.address
            """,
            (user.id,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_archived_properties(owner_id: int) -> list[dict]:
    rows = get_db().execute(
        """
        SELECT p.*, u.username AS owner_username, u.full_name AS owner_name
        FROM properties p JOIN users u ON u.id = p.owner_id
        WHERE p.owner_id = ? AND p.archived_at IS NOT NULL
        ORDER BY p.archived_at DESC
        """,
        (owner_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def archive_property(property_id: int, owner_id: int) -> None:
    db = get_db()
    row = db.execute(
        "SELECT id FROM properties WHERE id = ? AND owner_id = ?",
        (property_id, owner_id),
    ).fetchone()
    if not row:
        raise ValueError("Объект не найден или принадлежит другому арендодателю.")
    db.execute(
        "UPDATE properties SET archived_at = datetime('now') WHERE id = ? AND owner_id = ?",
        (property_id, owner_id),
    )
    log_audit("property.archive", "property", property_id, None, owner_id)


def unarchive_property(property_id: int, owner_id: int) -> None:
    db = get_db()
    row = db.execute(
        "SELECT id FROM properties WHERE id = ? AND owner_id = ?",
        (property_id, owner_id),
    ).fetchone()
    if not row:
        raise ValueError("Объект не найден или принадлежит другому арендодателю.")
    db.execute(
        "UPDATE properties SET archived_at = NULL WHERE id = ? AND owner_id = ?",
        (property_id, owner_id),
    )
    log_audit("property.unarchive", "property", property_id, None, owner_id)


def user_can_access_property(user: User, property_id: int) -> bool:
    db = get_db()
    if user.is_owner:
        row = db.execute(
            "SELECT 1 FROM properties WHERE id = ? AND owner_id = ?",
            (property_id, user.id),
        ).fetchone()
    else:
        row = db.execute(
            """
            SELECT 1 FROM tenant_properties
            WHERE tenant_id = ? AND property_id = ?
            """,
            (user.id, property_id),
        ).fetchone()
    return bool(row)


def get_dashboard_properties(user: User) -> list[dict]:
    """Список объектов с последними показаниями, счётом и задолженностью."""
    db = get_db()
    if user.is_owner:
        where, params = "p.owner_id = ? AND p.archived_at IS NULL", (user.id,)
    else:
        where, params = (
            "p.archived_at IS NULL AND "
            "p.id IN (SELECT property_id FROM tenant_properties WHERE tenant_id = ?)",
            (user.id,),
        )
    rows = db.execute(
        f"""
        WITH target AS (
            SELECT p.id FROM properties p WHERE {where}
        ),
        last_readings AS (
            SELECT r.*, ROW_NUMBER() OVER (
                       PARTITION BY r.property_id
                       ORDER BY r.reading_date DESC, r.id DESC
                   ) AS rn
            FROM meter_readings r
            WHERE r.property_id IN (SELECT id FROM target)
        ),
        last_invoices AS (
            SELECT i.*,
                   (SELECT COALESCE(SUM(p.amount), 0) FROM payments p
                    WHERE p.invoice_id = i.id) AS paid_amount,
                   ROW_NUMBER() OVER (
                       PARTITION BY i.property_id
                       ORDER BY r.reading_date DESC, i.id DESC
                   ) AS rn
            FROM invoices i JOIN meter_readings r ON r.id = i.reading_id
            WHERE i.property_id IN (SELECT id FROM target)
        ),
        debts AS (
            SELECT i.property_id,
                   COALESCE(SUM(
                       CASE WHEN i.paid = 0
                            THEN MAX(i.total - COALESCE((
                                SELECT SUM(p.amount) FROM payments p
                                WHERE p.invoice_id = i.id
                            ), 0), 0)
                            ELSE 0 END
                   ), 0) AS debt
            FROM invoices i
            WHERE i.property_id IN (SELECT id FROM target)
            GROUP BY i.property_id
        ),
        tenant_names AS (
            SELECT tp.property_id,
                   GROUP_CONCAT(COALESCE(NULLIF(u.full_name, ''), u.username), ', ') AS names
            FROM tenant_properties tp JOIN users u ON u.id = tp.tenant_id
            WHERE tp.property_id IN (SELECT id FROM target)
            GROUP BY tp.property_id
        )
        SELECT p.id, p.address, p.invite_code, p.created_at, p.archived_at,
               lr.reading_date, lr.hot_water, lr.cool_water, lr.electricity,
               li.id AS last_invoice_id, li.total AS last_invoice_total,
               li.paid AS last_invoice_paid,
               COALESCE(li.paid_amount, 0) AS last_invoice_paid_amount,
               COALESCE(d.debt, 0) AS debt,
               tn.names AS tenant_names,
               COALESCE(NULLIF(ow.full_name, ''), ow.username) AS owner_name
        FROM properties p
        LEFT JOIN users ow ON ow.id = p.owner_id
        LEFT JOIN last_readings lr ON lr.property_id = p.id AND lr.rn = 1
        LEFT JOIN last_invoices li ON li.property_id = p.id AND li.rn = 1
        LEFT JOIN debts d ON d.property_id = p.id
        LEFT JOIN tenant_names tn ON tn.property_id = p.id
        WHERE p.id IN (SELECT id FROM target)
        ORDER BY p.address
        """,
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def get_user_debt_summary(user: User) -> dict:
    properties = get_user_properties(user)
    if not properties:
        return {"total_debt": 0.0, "properties": 0}
    ids = [p["id"] for p in properties]
    placeholders = ",".join("?" for _ in ids)
    row = get_db().execute(
        f"""
        SELECT COALESCE(SUM(
                   CASE WHEN i.paid = 0
                        THEN MAX(i.total - COALESCE((
                            SELECT SUM(p.amount) FROM payments p
                            WHERE p.invoice_id = i.id
                        ), 0), 0)
                        ELSE 0 END
               ), 0) AS debt
        FROM invoices i
        WHERE i.property_id IN ({placeholders})
        """,
        ids,
    ).fetchone()
    return {"total_debt": money(row["debt"]), "properties": len(ids)}


# ---------------------------------------------------------------------------
# Арендаторы
# ---------------------------------------------------------------------------
def _normalize_shares(property_id: int, share: float | None = None,
                      tenant_id: int | None = None) -> None:
    """Задаёт долю арендатора или делит объект поровну между всеми."""
    db = get_db()
    if share is not None and tenant_id is not None:
        db.execute(
            "UPDATE tenant_properties SET share = ? WHERE property_id = ? AND tenant_id = ?",
            (round(float(share), 4), property_id, tenant_id),
        )
        return
    count = db.execute(
        "SELECT COUNT(*) AS c FROM tenant_properties WHERE property_id = ?",
        (property_id,),
    ).fetchone()["c"]
    if count:
        equal = round(1.0 / count, 4)
        db.execute(
            "UPDATE tenant_properties SET share = ? WHERE property_id = ?",
            (equal, property_id),
        )


def assign_tenant(property_id: int, username: str, owner_id: int,
                  share: float | None = None) -> dict:
    db = get_db()
    property_row = db.execute(
        "SELECT id, archived_at FROM properties WHERE id = ? AND owner_id = ?",
        (property_id, owner_id),
    ).fetchone()
    if not property_row:
        raise ValueError("Объект не найден или принадлежит другому арендодателю.")
    if property_row["archived_at"]:
        raise ValueError("Нельзя привязать арендатора к архивному объекту.")
    if share is not None:
        try:
            share = float(share)
        except (TypeError, ValueError) as exc:
            raise ValueError("Доля арендатора должна быть числом от 0 до 1.") from exc
        if not math.isfinite(share) or not 0 < share <= 1:
            raise ValueError("Доля арендатора должна быть больше 0 и не больше 1.")
    tenant = db.execute(
        "SELECT id, username, role, full_name FROM users WHERE username = ?",
        ((username or "").strip(),),
    ).fetchone()
    if not tenant or tenant["role"] != "tenant":
        raise ValueError("Арендатор с таким именем пользователя не найден.")
    db.execute(
        "INSERT OR IGNORE INTO tenant_properties (tenant_id, property_id) VALUES (?, ?)",
        (tenant["id"], property_id),
    )
    _normalize_shares(property_id, share, tenant["id"])
    log_audit("tenant.assign", "property", property_id,
              {"tenant_id": tenant["id"], "username": tenant["username"],
               "share": share}, owner_id)
    return dict(tenant)


def update_tenant_share(property_id: int, tenant_id: int, owner_id: int,
                        share: float | str) -> None:
    db = get_db()
    property_row = db.execute(
        "SELECT id FROM properties WHERE id = ? AND owner_id = ?",
        (property_id, owner_id),
    ).fetchone()
    if not property_row:
        raise ValueError("Объект не найден или принадлежит другому арендодателю.")
    try:
        share_value = float(share)
    except (TypeError, ValueError) as exc:
        raise ValueError("Доля арендатора должна быть числом от 0 до 1.") from exc
    if not math.isfinite(share_value) or not 0 < share_value <= 1:
        raise ValueError("Доля арендатора должна быть больше 0 и не больше 1.")
    updated = db.execute(
        "UPDATE tenant_properties SET share = ? WHERE property_id = ? AND tenant_id = ?",
        (round(share_value, 4), property_id, tenant_id),
    ).rowcount
    if not updated:
        raise ValueError("Арендатор не привязан к этому объекту.")
    log_audit("tenant.share", "property", property_id,
              {"tenant_id": tenant_id, "share": round(share_value, 4)}, owner_id)


def remove_tenant(property_id: int, tenant_id: int, owner_id: int) -> None:
    db = get_db()
    property_row = db.execute(
        "SELECT id FROM properties WHERE id = ? AND owner_id = ?",
        (property_id, owner_id),
    ).fetchone()
    if not property_row:
        raise ValueError("Объект не найден или принадлежит другому арендодателю.")
    db.execute(
        "DELETE FROM tenant_properties WHERE property_id = ? AND tenant_id = ?",
        (property_id, tenant_id),
    )
    log_audit("tenant.remove", "property", property_id, {"tenant_id": tenant_id}, owner_id)


def join_by_invite(tenant_id: int, invite_code: str) -> dict:
    code = (invite_code or "").strip().upper()
    if not code:
        raise ValueError("Введите код приглашения.")
    db = get_db()
    prop = db.execute(
        "SELECT id, address, archived_at FROM properties WHERE invite_code = ?",
        (code,),
    ).fetchone()
    if not prop:
        raise ValueError("Код приглашения не найден. Проверьте код у арендодателя.")
    if prop["archived_at"]:
        raise ValueError("Этот объект архивирован и не принимает новых арендаторов.")
    db.execute(
        "INSERT OR IGNORE INTO tenant_properties (tenant_id, property_id) VALUES (?, ?)",
        (tenant_id, prop["id"]),
    )
    _normalize_shares(prop["id"])
    log_audit("tenant.join", "property", prop["id"], {"invite_code": code}, tenant_id)
    return dict(prop)


def leave_property(tenant_id: int, property_id: int) -> None:
    get_db().execute(
        "DELETE FROM tenant_properties WHERE tenant_id = ? AND property_id = ?",
        (tenant_id, property_id),
    )
    log_audit("tenant.leave", "property", property_id, None, tenant_id)


def get_property_tenants(property_id: int) -> list[dict]:
    rows = get_db().execute(
        """
        SELECT u.id, u.username, u.full_name, u.email, tp.created_at AS assigned_at,
               tp.share
        FROM tenant_properties tp JOIN users u ON u.id = tp.tenant_id
        WHERE tp.property_id = ?
        ORDER BY u.username
        """,
        (property_id,),
    ).fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Показания и расчёт
# ---------------------------------------------------------------------------
def validate_readings(values: dict[str, Any]) -> dict[str, float | None]:
    result: dict[str, float | None] = {}
    for field in ("hot_water", "cool_water"):
        try:
            number = float(values.get(field))
        except (TypeError, ValueError) as exc:
            raise ValueError("Показания должны быть числами.") from exc
        if not math.isfinite(number):
            raise ValueError("Показания содержат некорректное значение.")
        if number < 0:
            raise ValueError("Показания не могут быть отрицательными.")
        if number > 1_000_000_000:
            raise ValueError("Показание слишком велико — проверьте введённое значение.")
        result[field] = quantity(number)

    day_raw = values.get("electricity_day")
    night_raw = values.get("electricity_night")
    day_empty = day_raw is None or str(day_raw).strip() == ""
    night_empty = night_raw is None or str(night_raw).strip() == ""
    if day_empty != night_empty:
        raise ValueError("Укажите оба показания электроэнергии: день и ночь, либо только общее.")
    if not day_empty:
        zones: dict[str, float] = {}
        for field, raw in (("electricity_day", day_raw), ("electricity_night", night_raw)):
            try:
                number = float(raw)
            except (TypeError, ValueError) as exc:
                raise ValueError("Показания должны быть числами.") from exc
            if not math.isfinite(number):
                raise ValueError("Показания содержат некорректное значение.")
            if number < 0:
                raise ValueError("Показания не могут быть отрицательными.")
            if number > 1_000_000_000:
                raise ValueError("Показание слишком велико — проверьте введённое значение.")
            zones[field] = quantity(number)
        result.update(zones)
        result["electricity"] = quantity(zones["electricity_day"] + zones["electricity_night"])
    else:
        try:
            number = float(values.get("electricity"))
        except (TypeError, ValueError) as exc:
            raise ValueError("Показания должны быть числами.") from exc
        if not math.isfinite(number):
            raise ValueError("Показания содержат некорректное значение.")
        if number < 0:
            raise ValueError("Показания не могут быть отрицательными.")
        if number > 1_000_000_000:
            raise ValueError("Показание слишком велико — проверьте введённое значение.")
        result["electricity"] = quantity(number)
        result["electricity_day"] = None
        result["electricity_night"] = None
    return result


def calculate_total(reading: dict, prev_reading: dict | None, tariff: dict,
                    charge_fixed_fee: bool = True) -> dict:
    """Рассчитывает счёт по показаниям и тарифу.

    Фиксированная плата начисляется только один раз за календарный месяц —
    вызывающая сторона передаёт ``charge_fixed_fee=False`` для повторных
    показаний месяца. Если в тарифе заданы зоны «день/ночь», а в показании —
    отдельные значения по зонам, электроэнергия считается по двум ставкам.
    """
    curr_hot = float(reading["hot_water"])
    curr_cold = float(reading["cool_water"])
    curr_el = float(reading["electricity"])
    baseline = prev_reading is None
    prev_hot = float(prev_reading["hot_water"]) if prev_reading else 0.0
    prev_cold = float(prev_reading["cool_water"]) if prev_reading else 0.0
    prev_el = float(prev_reading["electricity"]) if prev_reading else 0.0

    curr_day = _optional_value(reading, "electricity_day")
    curr_night = _optional_value(reading, "electricity_night")
    prev_day = _optional_value(prev_reading, "electricity_day")
    prev_night = _optional_value(prev_reading, "electricity_night")
    zone_mode = (
        has_electricity_zones(tariff)
        and curr_day is not None
        and curr_night is not None
        and (prev_reading is None or (prev_day is not None and prev_night is not None))
    )

    if not baseline:
        if curr_hot < prev_hot or curr_cold < prev_cold:
            raise ValueError("Новые показания не могут быть меньше предыдущих.")
        if zone_mode:
            if float(curr_day) < float(prev_day) or float(curr_night) < float(prev_night):
                raise ValueError("Новые показания не могут быть меньше предыдущих.")
        elif curr_el < prev_el:
            raise ValueError("Новые показания не могут быть меньше предыдущих.")

    # Первое показание является начальной точкой отсчёта: расход не начисляется,
    # к оплате предъявляется только фиксированная плата.
    diff_hot = 0.0 if baseline else quantity(curr_hot - prev_hot)
    diff_cold = 0.0 if baseline else quantity(curr_cold - prev_cold)
    if zone_mode:
        diff_day = 0.0 if baseline else quantity(float(curr_day) - float(prev_day))
        diff_night = 0.0 if baseline else quantity(float(curr_night) - float(prev_night))
        diff_el = quantity(diff_day + diff_night)
    else:
        diff_day = None
        diff_night = None
        diff_el = 0.0 if baseline else quantity(curr_el - prev_el)

    rate_el = float(tariff["tariff_el"])
    rate_water = float(tariff["tariff_water"])
    rate_com1 = float(tariff["tariff_com1"])
    rate_com2 = float(tariff["tariff_com2"])
    fixed_fee = float(tariff["fixed_fee"])
    rate_day = float(tariff["tariff_el_day"]) if has_electricity_zones(tariff) else None
    rate_night = float(tariff["tariff_el_night"]) if has_electricity_zones(tariff) else None

    if zone_mode and rate_day is not None and rate_night is not None:
        cost_el = money(diff_day * rate_day + diff_night * rate_night)
    else:
        cost_el = money(diff_el * rate_el)
    cost_hot = money(diff_hot * rate_water)
    cost_cold = money(diff_cold * rate_water)
    cost_com1 = money(diff_cold * rate_com1)
    cost_com2 = money((diff_cold + diff_hot) * rate_com2)
    fixed_component = money(fixed_fee) if charge_fixed_fee else 0.0
    total = money(cost_el + cost_hot + cost_cold + cost_com1 + cost_com2 + fixed_component)

    return {
        "baseline": baseline,
        "zones_active": zone_mode,
        "fixed_fee_charged": bool(charge_fixed_fee) and fixed_fee > 0,
        "reading": {
            "hot_water": curr_hot,
            "cool_water": curr_cold,
            "electricity": curr_el,
            "electricity_day": float(curr_day) if curr_day is not None else None,
            "electricity_night": float(curr_night) if curr_night is not None else None,
        },
        "previous": {
            "hot_water": prev_hot,
            "cool_water": prev_cold,
            "electricity": prev_el,
            "electricity_day": float(prev_day) if prev_day is not None else None,
            "electricity_night": float(prev_night) if prev_night is not None else None,
        },
        "consumption": {
            "hot_water": diff_hot,
            "cool_water": diff_cold,
            "electricity": diff_el,
            "electricity_day": diff_day,
            "electricity_night": diff_night,
        },
        "rates": {
            "tariff_el": rate_el,
            "tariff_water": rate_water,
            "tariff_com1": rate_com1,
            "tariff_com2": rate_com2,
            "fixed_fee": fixed_fee,
            "tariff_el_day": rate_day,
            "tariff_el_night": rate_night,
        },
        "components": {
            "electricity": cost_el,
            "hot_water": cost_hot,
            "cold_water": cost_cold,
            "wastewater_cold": cost_com1,
            "wastewater_total": cost_com2,
            "fixed_fee": fixed_component,
        },
        "total": total,
    }


def get_reading(reading_id: int) -> dict | None:
    row = get_db().execute(
        "SELECT * FROM meter_readings WHERE id = ?", (reading_id,)
    ).fetchone()
    return dict(row) if row else None


def get_previous_reading(property_id: int, reading_date: str,
                         exclude_id: int | None = None) -> dict | None:
    sql = """
        SELECT * FROM meter_readings
        WHERE property_id = ? AND reading_date < ?
    """
    params: list[Any] = [property_id, reading_date]
    if exclude_id is not None:
        sql += " AND id <> ?"
        params.append(exclude_id)
    sql += " ORDER BY reading_date DESC, id DESC LIMIT 1"
    row = get_db().execute(sql, params).fetchone()
    return dict(row) if row else None


def save_reading(property_id: int, user_id: int, reading_date: str,
                 values: dict[str, Any], reading_id: int | None = None) -> int:
    """Создаёт или обновляет показание и пересчитывает счета объекта."""
    reading_date = parse_iso_date(reading_date).isoformat()
    data = validate_readings(values)
    db = get_db()

    duplicate = db.execute(
        "SELECT id FROM meter_readings WHERE property_id = ? AND reading_date = ?",
        (property_id, reading_date),
    ).fetchone()
    if duplicate and duplicate["id"] != reading_id:
        raise ValueError(f"Показания за {reading_date} уже введены для этого объекта.")

    prev = get_previous_reading(property_id, reading_date, exclude_id=reading_id)
    if prev:
        if (data["hot_water"] < prev["hot_water"]
                or data["cool_water"] < prev["cool_water"]):
            raise ValueError("Новые показания не могут быть меньше предыдущих.")
        prev_day = _optional_value(prev, "electricity_day")
        prev_night = _optional_value(prev, "electricity_night")
        if (data["electricity_day"] is not None and prev_day is not None
                and data["electricity_night"] is not None and prev_night is not None):
            if (data["electricity_day"] < prev_day
                    or data["electricity_night"] < prev_night):
                raise ValueError("Новые показания не могут быть меньше предыдущих.")
        elif data["electricity"] < prev["electricity"]:
            raise ValueError("Новые показания не могут быть меньше предыдущих.")

    if reading_id is None:
        cur = db.execute(
            """
            INSERT INTO meter_readings
                (property_id, user_id, reading_date, hot_water, cool_water,
                 electricity, electricity_day, electricity_night)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (property_id, user_id, reading_date, data["hot_water"],
             data["cool_water"], data["electricity"],
             data["electricity_day"], data["electricity_night"]),
        )
        reading_id = cur.lastrowid
        log_audit("reading.create", "reading", reading_id,
                  {"property_id": property_id, "date": reading_date, **data}, user_id)
    else:
        db.execute(
            """
            UPDATE meter_readings
            SET reading_date = ?, hot_water = ?, cool_water = ?, electricity = ?,
                electricity_day = ?, electricity_night = ?
            WHERE id = ? AND property_id = ?
            """,
            (reading_date, data["hot_water"], data["cool_water"],
             data["electricity"], data["electricity_day"],
             data["electricity_night"], reading_id, property_id),
        )
        log_audit("reading.update", "reading", reading_id,
                  {"property_id": property_id, "date": reading_date, **data}, user_id)

    recalculate_property_invoices(property_id, user_id)
    return reading_id


def recalculate_property_invoices(property_id: int, user_id: int | None = None) -> int:
    """Пересчитывает все счета объекта по актуальным показаниям.

    Фиксированная плата начисляется только за первое показание каждого
    календарного месяца. Для каждого показания выбирается тариф, действовавший
    на дату показания. Если сумма счёта изменилась, уже внесённые платежи
    снимаются и требуется повторное подтверждение (факт фиксируется в audit_log).
    Коммит транзакции выполняет вызывающая сторона.
    """
    db = get_db()
    readings = db.execute(
        """
        SELECT * FROM meter_readings
        WHERE property_id = ?
        ORDER BY reading_date, id
        """,
        (property_id,),
    ).fetchall()

    recalculated = 0
    seen_months: set[str] = set()
    for index, reading in enumerate(readings):
        prev = readings[index - 1] if index else None
        tariff = get_tariff_for_date(property_id, reading["reading_date"])
        if not tariff:
            raise ValueError(
                f"Для даты {reading['reading_date']} не задан тариф. "
                "Добавьте тариф с более ранней датой начала действия."
            )
        month = reading["reading_date"][:7]
        charge_fixed_fee = month not in seen_months
        seen_months.add(month)
        details = calculate_total(
            dict(reading), dict(prev) if prev else None, tariff,
            charge_fixed_fee=charge_fixed_fee,
        )
        details["tariff_effective_from"] = tariff["effective_from"]

        existing = db.execute(
            """
            SELECT i.id, i.total, i.paid,
                   COALESCE((SELECT SUM(p.amount) FROM payments p
                             WHERE p.invoice_id = i.id), 0) AS paid_amount
            FROM invoices i WHERE i.reading_id = ?
            """,
            (reading["id"],),
        ).fetchone()
        if existing:
            total_changed = money(existing["total"]) != details["total"]
            if total_changed and (existing["paid"] or existing["paid_amount"] > 0):
                db.execute("DELETE FROM payments WHERE invoice_id = ?", (existing["id"],))
                db.execute(
                    """
                    UPDATE invoices
                    SET total = ?, details = ?, tariff_id = ?,
                        paid = 0, paid_at = NULL, paid_by = NULL,
                        updated_at = datetime('now')
                    WHERE id = ?
                    """,
                    (details["total"], json.dumps(details, ensure_ascii=False),
                     tariff["id"], existing["id"]),
                )
                log_audit("invoice.payment_revoked", "invoice", existing["id"],
                          {"reason": "recalculation", "old_total": existing["total"],
                           "new_total": details["total"],
                           "revoked_payments": money(existing["paid_amount"])}, user_id)
            else:
                db.execute(
                    """
                    UPDATE invoices
                    SET total = ?, details = ?, tariff_id = ?,
                        updated_at = datetime('now')
                    WHERE id = ?
                    """,
                    (details["total"], json.dumps(details, ensure_ascii=False),
                     tariff["id"], existing["id"]),
                )
        else:
            cur = db.execute(
                """
                INSERT INTO invoices
                    (property_id, reading_id, tariff_id, total, details)
                VALUES (?, ?, ?, ?, ?)
                """,
                (property_id, reading["id"], tariff["id"], details["total"],
                 json.dumps(details, ensure_ascii=False)),
            )
            log_audit("invoice.create", "invoice", cur.lastrowid,
                      {"reading_id": reading["id"], "total": details["total"]}, user_id)
        recalculated += 1
    return recalculated


def delete_reading(reading_id: int, user_id: int) -> int:
    db = get_db()
    reading = get_reading(reading_id)
    if not reading:
        raise ValueError("Показание не найдено.")
    property_id = reading["property_id"]
    db.execute("DELETE FROM meter_readings WHERE id = ?", (reading_id,))
    log_audit("reading.delete", "reading", reading_id,
              {"property_id": property_id, "date": reading["reading_date"]}, user_id)
    remaining = db.execute(
        "SELECT COUNT(*) AS c FROM meter_readings WHERE property_id = ?",
        (property_id,),
    ).fetchone()["c"]
    if remaining:
        recalculate_property_invoices(property_id, user_id)
    db.commit()
    return property_id


def get_invoice(invoice_id: int) -> dict | None:
    row = get_db().execute(
        """
        SELECT i.*, r.reading_date, r.hot_water, r.cool_water, r.electricity,
               r.electricity_day, r.electricity_night,
               p.address, p.owner_id, p.id AS property_id,
               COALESCE((SELECT SUM(pm.amount) FROM payments pm
                         WHERE pm.invoice_id = i.id), 0) AS paid_amount,
               COALESCE(NULLIF(u.full_name, ''), u.username) AS entered_by,
               pb.username AS paid_by_username,
               pu.full_name AS paid_by_name
        FROM invoices i
        JOIN meter_readings r ON r.id = i.reading_id
        JOIN properties p ON p.id = i.property_id
        JOIN users u ON u.id = r.user_id
        LEFT JOIN users pb ON pb.id = i.paid_by
        LEFT JOIN users pu ON pu.id = i.paid_by
        WHERE i.id = ?
        """,
        (invoice_id,),
    ).fetchone()
    return dict(row) if row else None


def get_invoice_details(invoice: dict) -> dict:
    try:
        return json.loads(invoice["details"])
    except (TypeError, ValueError, KeyError):
        return {}


def get_invoices(property_id: int, page: int = 1, per_page: int = 20,
                 date_from: str | None = None, date_to: str | None = None,
                 status: str | None = None) -> dict:
    db = get_db()
    per_page = min(max(1, per_page), 100)
    where = ["i.property_id = ?"]
    params: list[Any] = [property_id]
    if date_from:
        date_from = parse_iso_date(date_from).isoformat()
        where.append("r.reading_date >= ?")
        params.append(date_from)
    if date_to:
        date_to = parse_iso_date(date_to).isoformat()
        where.append("r.reading_date <= ?")
        params.append(date_to)
    paid_amount_sql = (
        "COALESCE((SELECT SUM(pm.amount) FROM payments pm "
        "WHERE pm.invoice_id = i.id), 0)"
    )
    if status == "paid":
        where.append("i.paid = 1")
    elif status == "unpaid":
        where.append(f"i.paid = 0 AND {paid_amount_sql} = 0")
    elif status == "partial":
        where.append(f"i.paid = 0 AND {paid_amount_sql} > 0")
    where_sql = " AND ".join(where)

    total = db.execute(
        f"""
        SELECT COUNT(*) AS c
        FROM invoices i JOIN meter_readings r ON r.id = i.reading_id
        WHERE {where_sql}
        """,
        params,
    ).fetchone()["c"]
    pages = max(1, (total + per_page - 1) // per_page)
    page = min(max(1, page), pages)
    offset = (page - 1) * per_page
    rows = db.execute(
        f"""
        SELECT i.*, r.reading_date, r.hot_water, r.cool_water, r.electricity,
               r.electricity_day, r.electricity_night,
               r.user_id, p.address, {paid_amount_sql} AS paid_amount,
               COALESCE(NULLIF(u.full_name, ''), u.username) AS entered_by,
               pb.username AS paid_by_username
        FROM invoices i
        JOIN meter_readings r ON r.id = i.reading_id
        JOIN properties p ON p.id = i.property_id
        JOIN users u ON u.id = r.user_id
        LEFT JOIN users pb ON pb.id = i.paid_by
        WHERE {where_sql}
        ORDER BY r.reading_date DESC, i.id DESC
        LIMIT ? OFFSET ?
        """,
        [*params, per_page, offset],
    ).fetchall()
    return {
        "items": [dict(row) for row in rows],
        "total": total,
        "page": page,
        "pages": pages,
        "per_page": per_page,
        "filters": {
            "date_from": date_from or "",
            "date_to": date_to or "",
            "status": status or "",
        },
    }


def get_invoice_payments(invoice_id: int) -> list[dict]:
    rows = get_db().execute(
        """
        SELECT p.*, COALESCE(NULLIF(u.full_name, ''), u.username) AS created_by_name,
               u.username AS created_by_username
        FROM payments p
        LEFT JOIN users u ON u.id = p.created_by
        WHERE p.invoice_id = ?
        ORDER BY p.paid_at DESC, p.id DESC
        """,
        (invoice_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _invoice_for_owner(invoice_id: int, owner_id: int) -> dict:
    invoice = get_invoice(invoice_id)
    if not invoice:
        raise ValueError("Счёт не найден.")
    if invoice["owner_id"] != owner_id:
        raise PermissionError("Вы не являетесь владельцем этого объекта.")
    return invoice


def add_invoice_payment(invoice_id: int, owner_id: int, amount: float | str,
                        paid_at: str | None = None,
                        comment: str | None = None) -> dict:
    """Добавляет частичный платёж. При полном покрытии счёт закрывается."""
    db = get_db()
    invoice = _invoice_for_owner(invoice_id, owner_id)
    if invoice["paid"]:
        raise ValueError("Счёт уже полностью оплачен.")
    try:
        amount_value = money(amount)
    except (TypeError, ValueError, ArithmeticError, InvalidOperation) as exc:
        raise ValueError("Сумма платежа должна быть числом.") from exc
    if amount_value <= 0:
        raise ValueError("Сумма платежа должна быть больше нуля.")
    paid_amount = money(invoice["paid_amount"])
    remaining = money(float(invoice["total"]) - paid_amount)
    if amount_value > remaining:
        raise ValueError(f"Сумма платежа превышает остаток {remaining:.2f} ₽.")
    if paid_at:
        paid_at = parse_iso_date(paid_at).isoformat()
    else:
        paid_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    comment = (comment or "").strip()[:300] or None
    db.execute(
        """
        INSERT INTO payments (invoice_id, amount, paid_at, created_by, comment)
        VALUES (?, ?, ?, ?, ?)
        """,
        (invoice_id, amount_value, paid_at, owner_id, comment),
    )
    new_paid_amount = money(paid_amount + amount_value)
    if new_paid_amount + 0.001 >= float(invoice["total"]):
        db.execute(
            """
            UPDATE invoices
            SET paid = 1, paid_at = datetime('now'), paid_by = ?,
                updated_at = datetime('now')
            WHERE id = ?
            """,
            (owner_id, invoice_id),
        )
    else:
        db.execute(
            "UPDATE invoices SET updated_at = datetime('now') WHERE id = ?",
            (invoice_id,),
        )
    log_audit("invoice.payment", "invoice", invoice_id,
              {"amount": amount_value, "paid_at": paid_at,
               "comment": comment, "paid_amount": new_paid_amount}, owner_id)
    return _invoice_for_owner(invoice_id, owner_id)


def delete_invoice_payment(payment_id: int, owner_id: int) -> dict:
    db = get_db()
    row = db.execute(
        """
        SELECT p.*, pr.owner_id AS owner_id
        FROM payments p
        JOIN invoices i ON i.id = p.invoice_id
        JOIN properties pr ON pr.id = i.property_id
        WHERE p.id = ?
        """,
        (payment_id,),
    ).fetchone()
    if not row:
        raise ValueError("Платёж не найден.")
    if row["owner_id"] != owner_id:
        raise PermissionError("Вы не являетесь владельцем этого объекта.")
    invoice_id = row["invoice_id"]
    db.execute("DELETE FROM payments WHERE id = ?", (payment_id,))
    db.execute(
        """
        UPDATE invoices
        SET paid = 0, paid_at = NULL, paid_by = NULL, updated_at = datetime('now')
        WHERE id = ?
        """,
        (invoice_id,),
    )
    log_audit("invoice.payment_delete", "invoice", invoice_id,
              {"payment_id": payment_id, "amount": row["amount"]}, owner_id)
    return _invoice_for_owner(invoice_id, owner_id)


def get_invoice_split(invoice: dict) -> dict:
    """Распределяет сумму счёта между арендаторами пропорционально долям."""
    tenants = get_property_tenants(invoice["property_id"])
    if not tenants:
        return {"tenants": [], "shares_sum": 0.0}
    shares = [max(0.0, float(t["share"] or 0)) for t in tenants]
    shares_sum = sum(shares)
    if shares_sum <= 0:
        weights = [1.0 / len(tenants)] * len(tenants)
    else:
        weights = [share / shares_sum for share in shares]
    total = float(invoice["total"])
    result = []
    allocated = 0.0
    for index, (tenant, weight) in enumerate(zip(tenants, weights, strict=True)):
        if index == len(tenants) - 1:
            amount = money(total - allocated)
        else:
            amount = money(total * weight)
            allocated = money(allocated + amount)
        result.append({
            "id": tenant["id"],
            "username": tenant["username"],
            "full_name": tenant["full_name"],
            "share": float(tenant["share"] or 0),
            "amount": amount,
        })
    return {"tenants": result, "shares_sum": money(shares_sum)}


def mark_invoice_paid(invoice_id: int, owner_id: int) -> dict:
    db = get_db()
    invoice = _invoice_for_owner(invoice_id, owner_id)
    if invoice["paid"]:
        raise ValueError("Счёт уже отмечен как оплаченный.")
    paid_amount = money(invoice["paid_amount"])
    remaining = money(float(invoice["total"]) - paid_amount)
    if remaining > 0:
        db.execute(
            """
            INSERT INTO payments (invoice_id, amount, paid_at, created_by, comment)
            VALUES (?, ?, datetime('now'), ?, 'Полная оплата счёта')
            """,
            (invoice_id, remaining, owner_id),
        )
    db.execute(
        """
        UPDATE invoices
        SET paid = 1, paid_at = datetime('now'), paid_by = ?,
            updated_at = datetime('now')
        WHERE id = ?
        """,
        (owner_id, invoice_id),
    )
    log_audit("invoice.paid", "invoice", invoice_id,
              {"total": invoice["total"], "paid_amount": money(paid_amount + remaining)},
              owner_id)
    return _invoice_for_owner(invoice_id, owner_id)


def mark_invoice_unpaid(invoice_id: int, owner_id: int) -> dict:
    db = get_db()
    invoice = _invoice_for_owner(invoice_id, owner_id)
    paid_amount = money(invoice["paid_amount"])
    db.execute("DELETE FROM payments WHERE invoice_id = ?", (invoice_id,))
    db.execute(
        """
        UPDATE invoices
        SET paid = 0, paid_at = NULL, paid_by = NULL, updated_at = datetime('now')
        WHERE id = ?
        """,
        (invoice_id,),
    )
    log_audit("invoice.unpaid", "invoice", invoice_id,
              {"total": invoice["total"], "removed_payments": paid_amount}, owner_id)
    return _invoice_for_owner(invoice_id, owner_id)


# ---------------------------------------------------------------------------
# Журнал действий
# ---------------------------------------------------------------------------
def get_audit_log(owner_id: int, limit: int = 200, action: str | None = None) -> list[dict]:
    """Действия арендодателя и арендаторов его объектов."""
    limit = min(max(1, limit), 500)
    where = [
        "(a.user_id = ? OR a.user_id IN ("
        "SELECT tp.tenant_id FROM tenant_properties tp "
        "JOIN properties p ON p.id = tp.property_id WHERE p.owner_id = ?))"
    ]
    params: list[Any] = [owner_id, owner_id]
    if action:
        where.append("a.action = ?")
        params.append(action)
    params.append(limit)
    rows = get_db().execute(
        f"""
        SELECT a.*, u.username,
               COALESCE(NULLIF(u.full_name, ''), u.username, 'Система') AS user_name
        FROM audit_log a LEFT JOIN users u ON u.id = a.user_id
        WHERE {' AND '.join(where)}
        ORDER BY a.id DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def get_audit_actions(owner_id: int) -> list[str]:
    rows = get_db().execute(
        """
        SELECT DISTINCT a.action
        FROM audit_log a
        WHERE a.user_id = ? OR a.user_id IN (
            SELECT tp.tenant_id FROM tenant_properties tp
            JOIN properties p ON p.id = tp.property_id WHERE p.owner_id = ?
        )
        ORDER BY a.action
        """,
        (owner_id, owner_id),
    ).fetchall()
    return [row["action"] for row in rows]


# ---------------------------------------------------------------------------
# Статистика
# ---------------------------------------------------------------------------
def month_range(first: str, last: str) -> list[str]:
    start = datetime.strptime(first, "%Y-%m").date().replace(day=1)
    end = datetime.strptime(last, "%Y-%m").date().replace(day=1)
    if start > end:
        return []
    result = []
    current = start
    while current <= end:
        result.append(current.strftime("%Y-%m"))
        current = (current.replace(day=28) + timedelta(days=4)).replace(day=1)
    return result


def get_stats_data(user: User, property_id: int | None, months_back: int) -> dict:
    """Агрегирует счета и расход по месяцам.

    property_id = None или 0 означает «все доступные объекты».
    months_back = 0 означает «всё время».
    """
    db = get_db()
    properties = get_user_properties(user)
    if property_id:
        properties = [p for p in properties if p["id"] == property_id]
    if not properties:
        return {
            "labels": [], "labels_display": [], "totals": [], "hot_water": [],
            "cold_water": [], "electricity": [],
            "summary": {"total_sum": 0, "avg_month": 0, "avg_hot": 0,
                        "avg_cold": 0, "avg_el": 0, "months": 0},
        }
    ids = [p["id"] for p in properties]
    placeholders = ",".join("?" for _ in ids)

    readings = db.execute(
        f"""
        SELECT id, property_id, reading_date, hot_water, cool_water, electricity
        FROM meter_readings
        WHERE property_id IN ({placeholders})
        ORDER BY property_id, reading_date, id
        """,
        ids,
    ).fetchall()
    prev_by_property: dict[int, Any] = {}
    consumption_by_reading: dict[int, dict] = {}
    all_months: set[str] = set()
    for reading in readings:
        pid = reading["property_id"]
        prev = prev_by_property.get(pid)
        if prev is None:
            # Первое показание — начальная точка отсчёта, расход по нему не начисляется.
            consumption = {"hot_water": 0.0, "cool_water": 0.0, "electricity": 0.0}
        else:
            consumption = {
                "hot_water": reading["hot_water"] - prev["hot_water"],
                "cool_water": reading["cool_water"] - prev["cool_water"],
                "electricity": reading["electricity"] - prev["electricity"],
            }
        consumption_by_reading[reading["id"]] = consumption
        prev_by_property[pid] = reading
        all_months.add(reading["reading_date"][:7])

    invoices = db.execute(
        f"""
        SELECT i.id, i.total, i.paid, r.id AS reading_id, r.reading_date
        FROM invoices i JOIN meter_readings r ON r.id = i.reading_id
        WHERE i.property_id IN ({placeholders})
        ORDER BY r.reading_date
        """,
        ids,
    ).fetchall()

    invoice_months = {row["reading_date"][:7] for row in invoices}
    all_months |= invoice_months
    if not all_months:
        return {
            "labels": [], "labels_display": [], "totals": [], "hot_water": [],
            "cold_water": [], "electricity": [],
            "summary": {"total_sum": 0, "avg_month": 0, "avg_hot": 0,
                        "avg_cold": 0, "avg_el": 0, "months": 0},
        }

    first_month = min(all_months)
    last_month = max(max(all_months), date.today().strftime("%Y-%m"))
    if months_back and months_back > 0:
        end = datetime.strptime(last_month, "%Y-%m").date().replace(day=1)
        start = end
        for _ in range(months_back - 1):
            start = (start - timedelta(days=1)).replace(day=1)
        first_month = max(first_month, start.strftime("%Y-%m"))
    months = month_range(first_month, last_month)

    totals = dict.fromkeys(months, 0.0)
    hot = dict.fromkeys(months, 0.0)
    cold = dict.fromkeys(months, 0.0)
    el = dict.fromkeys(months, 0.0)

    for invoice in invoices:
        month = invoice["reading_date"][:7]
        if month not in totals:
            continue
        totals[month] += invoice["total"]
        consumption = consumption_by_reading.get(invoice["reading_id"])
        if consumption:
            hot[month] += consumption["hot_water"]
            cold[month] += consumption["cool_water"]
            el[month] += consumption["electricity"]

    total_sum = money(sum(totals.values()))
    count = len(months) or 1
    summary = {
        "total_sum": total_sum,
        "avg_month": money(total_sum / count),
        "avg_hot": quantity(sum(hot.values()) / count),
        "avg_cold": quantity(sum(cold.values()) / count),
        "avg_el": quantity(sum(el.values()) / count),
        "months": len(months),
    }
    return {
        "labels": months,
        "labels_display": [
            f"{m[5:7]}.{m[:4]}" for m in months
        ],
        "totals": [money(totals[m]) for m in months],
        "hot_water": [quantity(hot[m]) for m in months],
        "cold_water": [quantity(cold[m]) for m in months],
        "electricity": [quantity(el[m]) for m in months],
        "summary": summary,
    }


def get_property_report_rows(property_id: int) -> list[dict]:
    """Строки для экспорта истории в CSV с расходом и деталями."""
    db = get_db()
    readings = db.execute(
        """
        SELECT r.*, i.id AS invoice_id, i.total, i.paid, i.paid_at,
               i.details,
               COALESCE((SELECT SUM(pm.amount) FROM payments pm
                         WHERE pm.invoice_id = i.id), 0) AS paid_amount,
               u.username AS entered_by
        FROM meter_readings r
        LEFT JOIN invoices i ON i.reading_id = r.id
        JOIN users u ON u.id = r.user_id
        WHERE r.property_id = ?
        ORDER BY r.reading_date
        """,
        (property_id,),
    ).fetchall()
    result = []
    prev = None
    for row in readings:
        item = dict(row)
        item["consumption_hot"] = quantity(row["hot_water"] - (prev["hot_water"] if prev else 0))
        item["consumption_cold"] = quantity(row["cool_water"] - (prev["cool_water"] if prev else 0))
        item["consumption_el"] = quantity(row["electricity"] - (prev["electricity"] if prev else 0))
        prev = row
        result.append(item)
    return result
