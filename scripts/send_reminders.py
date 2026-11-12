"""Отправка напоминаний арендаторам.

Запуск:
    python scripts/send_reminders.py

Требует настроенных SMTP-переменных и NOTIFICATIONS_ENABLED=1.
"""
from __future__ import annotations

import sys
from pathlib import Path


def main() -> None:
    base_dir = Path(__file__).resolve().parent.parent
    if str(base_dir) not in sys.path:
        sys.path.insert(0, str(base_dir))

    from app import create_app
    from notifications import send_payment_reminders, send_reading_reminders

    app = create_app()
    with app.app_context():
        payments = send_payment_reminders()
        readings = send_reading_reminders()
        print(payments["message"])
        print(readings["message"])


if __name__ == "__main__":
    main()
