"""Наполнение демонстрационной базы данных.

Запуск:
    python seed_demo.py

Скрипт удаляет файл instance/utility_billing.db и создаёт новый набор данных:
два арендодателя, три арендатора, три объекта с разными тарифами,
история показаний за 9 месяцев и частично оплаченные счета.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from app import create_app
from extensions import get_db
from models import (add_tariff, assign_tenant, create_property,
                    hash_password, recalculate_property_invoices)

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "instance" / "utility_billing.db"


def reset_database() -> None:
    for suffix in ("", "-wal", "-shm"):
        path = Path(str(DB_PATH) + suffix)
        if path.exists():
            path.unlink()


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

        prop1 = create_property(
            ids["owner_demo"],
            "г. Челябинск, ул. Кирова, д. 15, кв. 42",
            "2025-01-01",
            {"tariff_el": 4.28, "tariff_water": 42.50,
             "tariff_com1": 32.10, "tariff_com2": 27.40, "fixed_fee": 17000},
        )
        prop2 = create_property(
            ids["owner_demo"],
            "г. Челябинск, пр. Ленина, д. 100, кв. 7",
            "2025-01-01",
            {"tariff_el": 3.95, "tariff_water": 38.20,
             "tariff_com1": 29.80, "tariff_com2": 24.10, "fixed_fee": 15500},
        )
        prop3 = create_property(
            ids["owner_second"],
            "г. Челябинск, ул. Молодогвардейцев, д. 33, кв. 11",
            "2025-01-01",
            {"tariff_el": 5.10, "tariff_water": 46.80,
             "tariff_com1": 35.50, "tariff_com2": 30.20, "fixed_fee": 19000},
        )

        # Вторая версия тарифа для первого объекта — демонстрация версионирования.
        add_tariff(prop1, "2026-07-01", {
            "tariff_el": 4.65, "tariff_water": 45.10,
            "tariff_com1": 34.00, "tariff_com2": 29.00, "fixed_fee": 18000,
        }, ids["owner_demo"])

        assign_tenant(prop1, "tenant_demo", ids["owner_demo"])
        assign_tenant(prop2, "tenant_second", ids["owner_demo"])
        assign_tenant(prop3, "tenant_second", ids["owner_second"])
        assign_tenant(prop1, "tenant_third", ids["owner_demo"])

        # Показания за март–октябрь 2026 года. Значения растут, расход реалистичен.
        readings = {
            prop1: [
                ("2026-03-28", 120.4, 210.1, 5400),
                ("2026-04-28", 124.8, 216.5, 5580),
                ("2026-05-28", 129.1, 222.4, 5745),
                ("2026-06-28", 133.9, 229.0, 5920),
                ("2026-07-28", 137.2, 234.1, 6155),
                ("2026-08-28", 141.0, 240.3, 6420),
                ("2026-09-28", 144.7, 246.0, 6635),
                ("2026-10-28", 149.3, 252.8, 6890),
                ("2026-11-01", 152.1, 258.2, 7110),
            ],
            prop2: [
                ("2026-03-25", 86.0, 150.2, 3200),
                ("2026-04-25", 89.2, 155.0, 3340),
                ("2026-05-25", 92.4, 159.8, 3475),
                ("2026-06-25", 95.9, 165.1, 3605),
                ("2026-07-25", 98.1, 169.2, 3780),
                ("2026-08-25", 101.5, 174.0, 3950),
                ("2026-09-25", 104.0, 178.6, 4090),
                ("2026-10-25", 107.8, 184.2, 4270),
                ("2026-11-01", 110.4, 189.3, 4400),
            ],
            prop3: [
                ("2026-03-30", 65.0, 110.0, 2500),
                ("2026-04-30", 67.5, 114.2, 2620),
                ("2026-05-30", 70.1, 118.0, 2735),
                ("2026-06-30", 72.0, 121.6, 2840),
                ("2026-07-30", 74.9, 126.2, 2985),
                ("2026-08-30", 77.1, 130.4, 3100),
                ("2026-09-30", 79.8, 134.9, 3255),
                ("2026-10-30", 82.2, 139.0, 3395),
                ("2026-11-01", 84.1, 142.5, 3500),
            ],
        }
        for property_id, rows in readings.items():
            for reading_date, hot, cold, electricity in rows:
                db.execute(
                    """
                    INSERT INTO meter_readings
                        (property_id, user_id, reading_date, hot_water, cool_water, electricity)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (property_id, ids["tenant_demo"] if property_id == prop1 else ids["tenant_second"],
                     reading_date, hot, cold, electricity),
                )

        db.commit()
        for property_id in (prop1, prop2, prop3):
            recalculate_property_invoices(property_id, ids["owner_demo"])

        # Первые пять счетов каждого объекта считаем оплаченными.
        for property_id, owner_id in ((prop1, ids["owner_demo"]),
                                      (prop2, ids["owner_demo"]),
                                      (prop3, ids["owner_second"])):
            db.execute(
                """
                UPDATE invoices
                SET paid = 1, paid_at = datetime('now'), paid_by = ?
                WHERE property_id = ? AND reading_id IN (
                    SELECT id FROM meter_readings WHERE property_id = ?
                    ORDER BY reading_date LIMIT 5
                )
                """,
                (owner_id, property_id, property_id),
            )
        db.commit()

        print("Демо-данные созданы.")
        print("Арендодатели: owner_demo / owner_second, пароль demo12345")
        print("Арендаторы:   tenant_demo / tenant_second / tenant_third, пароль demo12345")


if __name__ == "__main__":
    main()
