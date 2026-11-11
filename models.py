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
import secrets
import string
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Iterable

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
        self.full_name = row["full_name"] or ""
        self.email = row["email"] or ""
        self.created_at = row["created_at"] if "created_at" in row.keys() else None

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
    with open(schema_path, "r", encoding="utf-8") as fh:
        db.executescript(fh.read())
    db.commit()


def table_columns(table: str) -> set[str]:
    return {row["name"] for row in get_db().execute(f"PRAGMA table_info({table})").fetchall()}


def database_is_legacy() -> bool:
    """Определяет старую схему (тарифы хранились в properties, не было JSON)."""
    tables = {
        row["name"]
        for row in get_db().execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    if "properties" not in tables or "invoices" not in tables:
        return False
    columns = table_columns("properties")
    return "tariff_el" in columns and "property_tariffs" not in tables


# ---------------------------------------------------------------------------
# Тарифы
# ---------------------------------------------------------------------------
TARIFF_FIELDS = ("tariff_el", "tariff_water", "tariff_com1", "tariff_com2", "fixed_fee")


def validate_tariff_values(values: dict[str, Any]) -> dict[str, float]:
    result: dict[str, float] = {}
    for field in TARIFF_FIELDS:
        raw = values.get(field)
        try:
            number = float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Поле «{field}» должно быть числом.") from exc
        if number < 0:
            raise ValueError("Тарифы и фиксированная плата не могут быть отрицательными.")
        if number > 10_000_000:
            raise ValueError("Значение тарифа слишком велико.")
        result[field] = round(number, 4)
    return result


def add_tariff(property_id: int, effective_from: str, values: dict[str, Any],
               user_id: int | None = None) -> int:
    parse_iso_date(effective_from)
    data = validate_tariff_values(values)
    db = get_db()
    db.execute(
        """
        INSERT INTO property_tariffs
            (property_id, effective_from, tariff_el, tariff_water,
             tariff_com1, tariff_com2, fixed_fee)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(property_id, effective_from) DO UPDATE SET
            tariff_el    = excluded.tariff_el,
            tariff_water = excluded.tariff_water,
            tariff_com1  = excluded.tariff_com1,
            tariff_com2  = excluded.tariff_com2,
            fixed_fee    = excluded.fixed_fee
        """,
        (
            property_id,
            effective_from,
            data["tariff_el"],
            data["tariff_water"],
            data["tariff_com1"],
            data["tariff_com2"],
            data["fixed_fee"],
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
    parse_iso_date(on_date)
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
    parse_iso_date(tariff_effective_from)
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


def get_user_properties(user: User) -> list[dict]:
    db = get_db()
    if user.is_owner:
        rows = db.execute(
            """
            SELECT p.*, u.username AS owner_username, u.full_name AS owner_name
            FROM properties p JOIN users u ON u.id = p.owner_id
            WHERE p.owner_id = ?
            ORDER BY p.address
            """,
            (user.id,),
        ).fetchall()
    else:
        rows = db.execute(
            """
            SELECT p.*, u.username AS owner_username, u.full_name AS owner_name
            FROM properties p
            JOIN users u ON u.id = p.owner_id
            JOIN tenant_properties tp ON tp.property_id = p.id
            WHERE tp.tenant_id = ?
            ORDER BY p.address
            """,
            (user.id,),
        ).fetchall()
    return [dict(row) for row in rows]


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


def get_property_for_user(user: User, property_id: int) -> dict | None:
    if not user_can_access_property(user, property_id):
        return None
    return get_property(property_id)


def get_dashboard_properties(user: User) -> list[dict]:
    """Список объектов с последними показаниями, счётом и задолженностью."""
    db = get_db()
    if user.is_owner:
        where, params = "p.owner_id = ?", (user.id,)
    else:
        where, params = (
            "p.id IN (SELECT property_id FROM tenant_properties WHERE tenant_id = ?)",
            (user.id,),
        )
    rows = db.execute(
        f"""
        WITH last_readings AS (
            SELECT r.*, ROW_NUMBER() OVER (
                       PARTITION BY r.property_id
                       ORDER BY r.reading_date DESC, r.id DESC
                   ) AS rn
            FROM meter_readings r
        ),
        last_invoices AS (
            SELECT i.*, ROW_NUMBER() OVER (
                       PARTITION BY i.property_id
                       ORDER BY r.reading_date DESC, i.id DESC
                   ) AS rn
            FROM invoices i JOIN meter_readings r ON r.id = i.reading_id
        ),
        debts AS (
            SELECT property_id, COALESCE(SUM(total), 0) AS debt
            FROM invoices WHERE paid = 0 GROUP BY property_id
        ),
        tenant_names AS (
            SELECT tp.property_id,
                   GROUP_CONCAT(COALESCE(NULLIF(u.full_name, ''), u.username), ', ') AS names
            FROM tenant_properties tp JOIN users u ON u.id = tp.tenant_id
            GROUP BY tp.property_id
        )
        SELECT p.id, p.address, p.invite_code, p.created_at,
               lr.reading_date, lr.hot_water, lr.cool_water, lr.electricity,
               li.id AS last_invoice_id, li.total AS last_invoice_total,
               li.paid AS last_invoice_paid,
               COALESCE(d.debt, 0) AS debt,
               tn.names AS tenant_names,
               COALESCE(NULLIF(ow.full_name, ''), ow.username) AS owner_name
        FROM properties p
        LEFT JOIN users ow ON ow.id = p.owner_id
        LEFT JOIN last_readings lr ON lr.property_id = p.id AND lr.rn = 1
        LEFT JOIN last_invoices li ON li.property_id = p.id AND li.rn = 1
        LEFT JOIN debts d ON d.property_id = p.id
        LEFT JOIN tenant_names tn ON tn.property_id = p.id
        WHERE {where}
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
        f"SELECT COALESCE(SUM(total), 0) AS debt FROM invoices WHERE paid = 0 AND property_id IN ({placeholders})",
        ids,
    ).fetchone()
    return {"total_debt": money(row["debt"]), "properties": len(ids)}


# ---------------------------------------------------------------------------
# Арендаторы
# ---------------------------------------------------------------------------
def assign_tenant(property_id: int, username: str, owner_id: int) -> dict:
    db = get_db()
    property_row = db.execute(
        "SELECT id FROM properties WHERE id = ? AND owner_id = ?",
        (property_id, owner_id),
    ).fetchone()
    if not property_row:
        raise ValueError("Объект не найден или принадлежит другому арендодателю.")
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
    log_audit("tenant.assign", "property", property_id,
              {"tenant_id": tenant["id"], "username": tenant["username"]}, owner_id)
    return dict(tenant)


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
        "SELECT id, address FROM properties WHERE invite_code = ?",
        (code,),
    ).fetchone()
    if not prop:
        raise ValueError("Код приглашения не найден. Проверьте код у арендодателя.")
    db.execute(
        "INSERT OR IGNORE INTO tenant_properties (tenant_id, property_id) VALUES (?, ?)",
        (tenant_id, prop["id"]),
    )
    log_audit("tenant.join", "property", prop["id"], {"invite_code": code}, tenant_id)
    return dict(prop)


def leave_property(tenant_id: int, property_id: int) -> None:
    get_db().execute(
        "DELETE FROM tenant_properties WHERE tenant_id = ? AND property_id = ?",
        (tenant_id, property_id),
    )
    log_audit("tenant.leave", "property", property_id, None, tenant_id)


def get_tenants_for_owner(owner_id: int) -> list[dict]:
    rows = get_db().execute(
        """
        SELECT u.id, u.username, u.full_name, u.email, u.created_at,
               p.id AS property_id, p.address,
               tp.created_at AS assigned_at
        FROM tenant_properties tp
        JOIN users u ON u.id = tp.tenant_id
        JOIN properties p ON p.id = tp.property_id
        WHERE p.owner_id = ?
        ORDER BY p.address, u.username
        """,
        (owner_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def get_all_tenants() -> list[dict]:
    rows = get_db().execute(
        """
        SELECT id, username, full_name, email, created_at
        FROM users WHERE role = 'tenant' ORDER BY username
        """
    ).fetchall()
    return [dict(row) for row in rows]


def get_property_tenants(property_id: int) -> list[dict]:
    rows = get_db().execute(
        """
        SELECT u.id, u.username, u.full_name, u.email, tp.created_at AS assigned_at
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
def validate_readings(values: dict[str, Any]) -> dict[str, float]:
    result: dict[str, float] = {}
    for field in ("hot_water", "cool_water", "electricity"):
        try:
            number = float(values.get(field))
        except (TypeError, ValueError) as exc:
            raise ValueError("Показания должны быть числами.") from exc
        if number < 0:
            raise ValueError("Показания не могут быть отрицательными.")
        if number > 1_000_000_000:
            raise ValueError("Показание слишком велико — проверьте введённое значение.")
        result[field] = quantity(number)
    return result


def calculate_total(reading: dict, prev_reading: dict | None, tariff: dict) -> dict:
    """Рассчитывает счёт по показаниям и тарифу.

    Возвращает словарь с расходом, ставками, слагаемыми и итогом.
    """
    curr_hot = float(reading["hot_water"])
    curr_cold = float(reading["cool_water"])
    curr_el = float(reading["electricity"])
    baseline = prev_reading is None
    prev_hot = float(prev_reading["hot_water"]) if prev_reading else 0.0
    prev_cold = float(prev_reading["cool_water"]) if prev_reading else 0.0
    prev_el = float(prev_reading["electricity"]) if prev_reading else 0.0

    if not baseline and (curr_hot < prev_hot or curr_cold < prev_cold or curr_el < prev_el):
        raise ValueError("Новые показания не могут быть меньше предыдущих.")

    # Первое показание является начальной точкой отсчёта: расход не начисляется,
    # к оплате предъявляется только фиксированная плата.
    diff_hot = 0.0 if baseline else quantity(curr_hot - prev_hot)
    diff_cold = 0.0 if baseline else quantity(curr_cold - prev_cold)
    diff_el = 0.0 if baseline else quantity(curr_el - prev_el)

    rate_el = float(tariff["tariff_el"])
    rate_water = float(tariff["tariff_water"])
    rate_com1 = float(tariff["tariff_com1"])
    rate_com2 = float(tariff["tariff_com2"])
    fixed_fee = float(tariff["fixed_fee"])

    cost_el = money(diff_el * rate_el)
    cost_hot = money(diff_hot * rate_water)
    cost_cold = money(diff_cold * rate_water)
    cost_com1 = money(diff_cold * rate_com1)
    cost_com2 = money((diff_cold + diff_hot) * rate_com2)
    total = money(cost_el + cost_hot + cost_cold + cost_com1 + cost_com2 + fixed_fee)

    return {
        "baseline": baseline,
        "reading": {
            "hot_water": curr_hot,
            "cool_water": curr_cold,
            "electricity": curr_el,
        },
        "previous": {
            "hot_water": prev_hot,
            "cool_water": prev_cold,
            "electricity": prev_el,
        },
        "consumption": {
            "hot_water": diff_hot,
            "cool_water": diff_cold,
            "electricity": diff_el,
        },
        "rates": {
            "tariff_el": rate_el,
            "tariff_water": rate_water,
            "tariff_com1": rate_com1,
            "tariff_com2": rate_com2,
            "fixed_fee": fixed_fee,
        },
        "components": {
            "electricity": cost_el,
            "hot_water": cost_hot,
            "cold_water": cost_cold,
            "wastewater_cold": cost_com1,
            "wastewater_total": cost_com2,
            "fixed_fee": money(fixed_fee),
        },
        "total": total,
    }


def get_reading(reading_id: int) -> dict | None:
    row = get_db().execute(
        "SELECT * FROM meter_readings WHERE id = ?", (reading_id,)
    ).fetchone()
    return dict(row) if row else None


def get_readings(property_id: int, limit: int | None = None) -> list[dict]:
    sql = """
        SELECT r.*, u.username AS entered_by, u.full_name AS entered_by_name
        FROM meter_readings r JOIN users u ON u.id = r.user_id
        WHERE r.property_id = ?
        ORDER BY r.reading_date, r.id
    """
    params: list[Any] = [property_id]
    rows = get_db().execute(sql, params).fetchall()
    result = [dict(row) for row in rows]
    return result[-limit:] if limit else result


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
    parse_iso_date(reading_date)
    data = validate_readings(values)
    db = get_db()

    duplicate = db.execute(
        "SELECT id FROM meter_readings WHERE property_id = ? AND reading_date = ?",
        (property_id, reading_date),
    ).fetchone()
    if duplicate and duplicate["id"] != reading_id:
        raise ValueError(f"Показания за {reading_date} уже введены для этого объекта.")

    prev = get_previous_reading(property_id, reading_date, exclude_id=reading_id)
    if prev and (
        data["hot_water"] < prev["hot_water"]
        or data["cool_water"] < prev["cool_water"]
        or data["electricity"] < prev["electricity"]
    ):
        raise ValueError("Новые показания не могут быть меньше предыдущих.")

    if reading_id is None:
        cur = db.execute(
            """
            INSERT INTO meter_readings
                (property_id, user_id, reading_date, hot_water, cool_water, electricity)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (property_id, user_id, reading_date, data["hot_water"],
             data["cool_water"], data["electricity"]),
        )
        reading_id = cur.lastrowid
        log_audit("reading.create", "reading", reading_id,
                  {"property_id": property_id, "date": reading_date, **data}, user_id)
    else:
        db.execute(
            """
            UPDATE meter_readings
            SET reading_date = ?, hot_water = ?, cool_water = ?, electricity = ?
            WHERE id = ? AND property_id = ?
            """,
            (reading_date, data["hot_water"], data["cool_water"],
             data["electricity"], reading_id, property_id),
        )
        log_audit("reading.update", "reading", reading_id,
                  {"property_id": property_id, "date": reading_date, **data}, user_id)

    recalculate_property_invoices(property_id, user_id)
    return reading_id


def recalculate_property_invoices(property_id: int, user_id: int | None = None) -> int:
    """Пересчитывает все счета объекта по актуальным показаниям.

    Для каждого показания выбирается тариф, действовавший на дату показания.
    Если сумма уже оплаченного счёта изменилась, оплата снимается и требуется
    повторное подтверждение арендодателем (факт фиксируется в audit_log).
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
    for index, reading in enumerate(readings):
        prev = readings[index - 1] if index else None
        tariff = get_tariff_for_date(property_id, reading["reading_date"])
        if not tariff:
            raise ValueError(
                f"Для даты {reading['reading_date']} не задан тариф. "
                "Добавьте тариф с более ранней датой начала действия."
            )
        details = calculate_total(dict(reading), dict(prev) if prev else None, tariff)
        details["tariff_effective_from"] = tariff["effective_from"]

        existing = db.execute(
            "SELECT id, total, paid FROM invoices WHERE reading_id = ?",
            (reading["id"],),
        ).fetchone()
        if existing:
            total_changed = money(existing["total"]) != details["total"]
            if total_changed and existing["paid"]:
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
                           "new_total": details["total"]}, user_id)
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
            db.execute(
                """
                INSERT INTO invoices
                    (property_id, reading_id, tariff_id, total, details)
                VALUES (?, ?, ?, ?, ?)
                """,
                (property_id, reading["id"], tariff["id"], details["total"],
                 json.dumps(details, ensure_ascii=False)),
            )
            log_audit("invoice.create", "invoice", None,
                      {"reading_id": reading["id"], "total": details["total"]}, user_id)
        recalculated += 1
    db.commit()
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
               p.address, p.owner_id, p.id AS property_id,
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


def get_invoices(property_id: int, page: int = 1, per_page: int = 20) -> dict:
    db = get_db()
    total = db.execute(
        "SELECT COUNT(*) AS c FROM invoices WHERE property_id = ?", (property_id,)
    ).fetchone()["c"]
    pages = max(1, (total + per_page - 1) // per_page)
    page = min(max(1, page), pages)
    offset = (page - 1) * per_page
    rows = db.execute(
        """
        SELECT i.*, r.reading_date, r.hot_water, r.cool_water, r.electricity,
               r.user_id,
               COALESCE(NULLIF(u.full_name, ''), u.username) AS entered_by,
               pb.username AS paid_by_username
        FROM invoices i
        JOIN meter_readings r ON r.id = i.reading_id
        JOIN users u ON u.id = r.user_id
        LEFT JOIN users pb ON pb.id = i.paid_by
        WHERE i.property_id = ?
        ORDER BY r.reading_date DESC, i.id DESC
        LIMIT ? OFFSET ?
        """,
        (property_id, per_page, offset),
    ).fetchall()
    return {
        "items": [dict(row) for row in rows],
        "total": total,
        "page": page,
        "pages": pages,
        "per_page": per_page,
    }


def mark_invoice_paid(invoice_id: int, owner_id: int) -> dict:
    db = get_db()
    invoice = get_invoice(invoice_id)
    if not invoice:
        raise ValueError("Счёт не найден.")
    if invoice["owner_id"] != owner_id:
        raise PermissionError("Вы не являетесь владельцем этого объекта.")
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
              {"total": invoice["total"]}, owner_id)
    return invoice


def mark_invoice_unpaid(invoice_id: int, owner_id: int) -> dict:
    db = get_db()
    invoice = get_invoice(invoice_id)
    if not invoice:
        raise ValueError("Счёт не найден.")
    if invoice["owner_id"] != owner_id:
        raise PermissionError("Вы не являетесь владельцем этого объекта.")
    db.execute(
        """
        UPDATE invoices
        SET paid = 0, paid_at = NULL, paid_by = NULL, updated_at = datetime('now')
        WHERE id = ?
        """,
        (invoice_id,),
    )
    log_audit("invoice.unpaid", "invoice", invoice_id,
              {"total": invoice["total"]}, owner_id)
    return invoice


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

    totals = {m: 0.0 for m in months}
    hot = {m: 0.0 for m in months}
    cold = {m: 0.0 for m in months}
    el = {m: 0.0 for m in months}

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
               i.details, u.username AS entered_by
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
