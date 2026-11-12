"""Вспомогательные функции для тестов."""
from __future__ import annotations

from extensions import get_db
from models import save_reading


def add_reading(app, property_id, user_id, reading_date="2026-05-01",
                hot=100, cold=200, electricity=300, day=None, night=None):
    """Добавляет показание и возвращает (reading_id, invoice_id)."""
    values = {
        "hot_water": hot,
        "cool_water": cold,
        "electricity": electricity,
    }
    if day is not None or night is not None:
        values["electricity_day"] = day
        values["electricity_night"] = night
    with app.app_context():
        reading_id = save_reading(property_id, user_id, reading_date, values)
        db = get_db()
        db.commit()
        invoice = db.execute(
            "SELECT id FROM invoices WHERE reading_id = ?", (reading_id,)
        ).fetchone()
    return reading_id, invoice["id"] if invoice else None
