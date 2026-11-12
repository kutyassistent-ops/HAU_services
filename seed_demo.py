"""Наполнение демонстрационной базы данных.

Запуск:
    python seed_demo.py

Скрипт удаляет файл instance/utility_billing.db и создаёт новый набор данных:
два арендодателя, три арендатора, три объекта с разными тарифами,
история показаний за 9 месяцев, тарифные зоны день/ночь,
частичные оплаты и журнал действий. Даты генерируются относительно текущей.
"""
from __future__ import annotations

import calendar
import os
from datetime import date
from pathlib import Path

from app import create_app
from extensions import get_db
from models import (
    add_invoice_payment,
    add_tariff,
    assign_tenant,
    create_property,
    hash_password,
    recalculate_property_invoices,
)

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(
    os.environ.get("DATABASE_PATH", str(BASE_DIR / "instance" / "utility_billing.db"))
)


def reset_database() -> None:
    for suffix in ("", "-wal", "-shm"):
        path = Path(str(DB_PATH) + suffix)
        if path.exists():
            path.unlink()


def month_starts(count: int = 9) -> list[date]:
    """Первые числа последних ``count`` месяцев, включая текущий."""
    today = date.today().replace(day=1)
    result = []
    current = today
    for _ in range(count):
        result.append(current)
        current = (current - date.resolution).replace(day=1)
    return list(reversed(result))


def reading_date_for(month_start: date, day: int = 12) -> date | None:
    """Дата показания внутри месяца, не позже сегодняшнего дня."""
    last_day = calendar.monthrange(month_start.year, month_start.month)[1]
    candidate = month_start.replace(day=min(day, last_day))
    return candidate if candidate <= date.today() else None


def main() -> None:
    reset_database()
    app = create_app()
    with app.app_context():
        db = get_db()

        users = [
            ("owner_demo", "Иванов Пётр Романович", "owner", "owner@example.ru"),
            ("owner_second", "Смирнова Ольга Викторовна", "owner", "smirnova@example.ru"),
            ("tenant_demo", "Петрова Анна Сергеевна", "tenant", "petrova@example.ru"),
            ("tenant_second", "Кузнецов Максим Игоревич", "tenant", "kuznetsov@example.ru"),
            ("tenant_third", "Соколова Мария Андреевна", "tenant", "sokolova@example.ru"),
        ]
        ids = {}
        for username, full_name, role, email in users:
            cur = db.execute(
                """
                INSERT INTO users (username, password_hash, role, full_name, email)
                VALUES (?, ?, ?, ?, ?)
                """,
                (username, hash_password("demo12345"), role, full_name, email),
            )
            ids[username] = cur.lastrowid

        months = month_starts(9)
        start_date = months[0].isoformat()
        prop1 = create_property(
            ids["owner_demo"],
            "г. Челябинск, ул. Кирова, д. 15, кв. 42",
            start_date,
            {"tariff_el": 4.28, "tariff_water": 42.50,
             "tariff_com1": 32.10, "tariff_com2": 27.40, "fixed_fee": 17000,
             "tariff_el_day": 4.60, "tariff_el_night": 2.30},
        )
        prop2 = create_property(
            ids["owner_demo"],
            "г. Челябинск, пр. Ленина, д. 100, кв. 7",
            start_date,
            {"tariff_el": 3.95, "tariff_water": 38.20,
             "tariff_com1": 29.80, "tariff_com2": 24.10, "fixed_fee": 15500},
        )
        prop3 = create_property(
            ids["owner_second"],
            "г. Челябинск, ул. Молодогвардейцев, д. 33, кв. 11",
            start_date,
            {"tariff_el": 5.10, "tariff_water": 46.80,
             "tariff_com1": 35.50, "tariff_com2": 30.20, "fixed_fee": 19000},
        )

        # Вторая версия тарифа первого объекта — демонстрация версионирования
        # и автоматического пересчёта при сохранении тарифа.
        if len(months) >= 4:
            add_tariff(prop1, months[-4].isoformat(), {
                "tariff_el": 4.65, "tariff_water": 45.10,
                "tariff_com1": 34.00, "tariff_com2": 29.00, "fixed_fee": 18000,
                "tariff_el_day": 4.95, "tariff_el_night": 2.55,
            }, ids["owner_demo"])

        assign_tenant(prop1, "tenant_demo", ids["owner_demo"])
        assign_tenant(prop2, "tenant_second", ids["owner_demo"])
        assign_tenant(prop3, "tenant_second", ids["owner_second"])
        assign_tenant(prop1, "tenant_third", ids["owner_demo"])

        property_specs = [
            (prop1, ids["tenant_demo"], 120.4, 210.1, 5400),
            (prop2, ids["tenant_second"], 86.0, 150.2, 3200),
            (prop3, ids["tenant_second"], 65.0, 110.0, 2500),
        ]
        for property_id, user_id, hot0, cold0, el0 in property_specs:
            for index, month in enumerate(months):
                day = reading_date_for(month)
                if day is None:
                    continue
                hot = round(hot0 + 4.2 * index, 1)
                cold = round(cold0 + 6.0 * index, 1)
                electricity = round(el0 + 180 * index, 1)
                day_value = round(electricity * 0.7, 1) if property_id == prop1 else None
                night_value = round(electricity - day_value, 1) if day_value is not None else None
                db.execute(
                    """
                    INSERT INTO meter_readings
                        (property_id, user_id, reading_date, hot_water, cool_water,
                         electricity, electricity_day, electricity_night)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (property_id, user_id, day.isoformat(), hot, cold,
                     electricity, day_value, night_value),
                )

        db.commit()
        for property_id in (prop1, prop2, prop3):
            recalculate_property_invoices(property_id, ids["owner_demo"])
        db.commit()

        # Первые пять счетов каждого объекта оплачены полностью, ещё два —
        # частично, чтобы в интерфейсе был виден статус «частично оплачено».
        for property_id, owner_id in ((prop1, ids["owner_demo"]),
                                      (prop2, ids["owner_demo"]),
                                      (prop3, ids["owner_second"])):
            invoice_rows = db.execute(
                """
                SELECT i.id, i.total FROM invoices i
                JOIN meter_readings r ON r.id = i.reading_id
                WHERE i.property_id = ?
                ORDER BY r.reading_date
                """,
                (property_id,),
            ).fetchall()
            for invoice in invoice_rows[:5]:
                add_invoice_payment(invoice["id"], owner_id, invoice["total"])
            for invoice in invoice_rows[5:7]:
                add_invoice_payment(invoice["id"], owner_id, round(invoice["total"] * 0.5, 2),
                                    comment="Частичная оплата")
        db.commit()

        print("Демо-данные созданы.")
        print("Арендодатели: owner_demo / owner_second, пароль demo12345")
        print("Арендаторы:   tenant_demo / tenant_second / tenant_third, пароль demo12345")
        print(f"Период показаний: {months[0].isoformat()} — {date.today().isoformat()}")


if __name__ == "__main__":
    main()
