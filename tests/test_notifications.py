"""Тесты email-уведомлений и напоминаний (SMTP подменяется)."""
from __future__ import annotations

import pytest

from extensions import get_db
from models import save_reading
from notifications import (
    _invoice_body,
    notifications_configured,
    notify_invoice_by_email,
    send_email,
    send_payment_reminders,
    send_reading_reminders,
)
from tests.helpers import add_reading


class FakeSMTP:
    instances: list[FakeSMTP] = []

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.sent = []
        self.tls = False
        self.logged = None
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def starttls(self):
        self.tls = True

    def login(self, username, password):
        self.logged = (username, password)

    def send_message(self, message):
        self.sent.append(message)


class BrokenSMTP:
    def __init__(self, *args, **kwargs):
        raise OSError("smtp недоступен")


@pytest.fixture()
def smtp(app, monkeypatch):
    FakeSMTP.instances = []
    monkeypatch.setattr("notifications.smtplib.SMTP", FakeSMTP)
    app.config.update(
        NOTIFICATIONS_ENABLED=True,
        SMTP_HOST="smtp.test",
        SMTP_PORT=2525,
        SMTP_FROM="no-reply@test.ru",
        SMTP_USE_TLS=True,
        SMTP_USER="user",
        SMTP_PASSWORD="pass",
    )
    return FakeSMTP


def test_notifications_disabled(app):
    with app.app_context():
        assert notifications_configured() is False
        result = send_email("user@example.ru", "Тема", "Текст")
        assert result["sent"] is False
        assert "отключены" in result["message"]
        assert notify_invoice_by_email(1)["sent"] is False
        assert send_payment_reminders()["sent"] == 0
        assert send_reading_reminders()["sent"] == 0


def test_send_email_configured(app, smtp):
    with app.app_context():
        assert notifications_configured() is True
        result = send_email("user@example.ru", "Тема", "Текст")
        assert result["sent"] is True
        message = smtp.instances[-1].sent[0]
        assert message["To"] == "user@example.ru"
        assert message["Subject"] == "Тема"
        assert "Текст" in message.get_content()
        assert smtp.instances[-1].tls is True
        assert smtp.instances[-1].logged == ("user", "pass")

        no_recipient = send_email("", "Тема", "Текст")
        assert no_recipient["sent"] is False
        assert "не указан email" in no_recipient["message"]


def test_send_email_failure(app, monkeypatch):
    monkeypatch.setattr("notifications.smtplib.SMTP", BrokenSMTP)
    app.config.update(
        NOTIFICATIONS_ENABLED=True,
        SMTP_HOST="smtp.test",
        SMTP_FROM="no-reply@test.ru",
    )
    with app.app_context():
        result = send_email("user@example.ru", "Тема", "Текст")
        assert result["sent"] is False
        assert "Не удалось отправить" in result["message"]


def test_notify_invoice_by_email(app, smtp, ids):
    _, invoice_id = add_reading(app, ids["prop1"], ids["tenant"], "2026-05-01")
    with app.app_context():
        db = get_db()
        assert notify_invoice_by_email(invoice_id)["sent"] is False
        db.execute(
            "UPDATE users SET email = 'owner@example.ru' WHERE id = ?", (ids["owner"],)
        )
        db.execute(
            "UPDATE users SET email = 'tenant@example.ru' WHERE id = ?", (ids["tenant"],)
        )
        db.commit()

        result = notify_invoice_by_email(invoice_id)
        assert result["sent"] is True
        assert "2 из 2" in result["message"]
        recipients = sorted(
            msg["To"] for instance in smtp.instances for msg in instance.sent
        )
        assert recipients == ["owner@example.ru", "tenant@example.ru"]
        subjects = [msg["Subject"] for instance in smtp.instances for msg in instance.sent]
        assert all("Счёт ЖКУ" in subject for subject in subjects)
        audit = db.execute(
            "SELECT * FROM audit_log WHERE action = 'invoice.notify'"
        ).fetchone()
        assert audit is not None
        assert notify_invoice_by_email(99999) == {
            "sent": False, "message": "Счёт не найден."
        }


def test_invoice_body_contains_consumption(app, ids):
    _, invoice_id = add_reading(app, ids["prop1"], ids["tenant"], "2026-05-01")
    add_reading(app, ids["prop1"], ids["tenant"], "2026-06-01", 102, 205, 320)
    with app.app_context():
        from models import get_invoice, get_invoice_details

        invoice = get_invoice(invoice_id)
        body = _invoice_body(invoice, get_invoice_details(invoice))
        assert "Сумма к оплате" in body
        assert "Горячая вода" in body
        assert "Электроэнергия" in body


def test_reminders(app, smtp, ids):
    _, invoice_id = add_reading(app, ids["prop1"], ids["tenant"], "2026-05-01")
    with app.app_context():
        db = get_db()
        db.execute(
            "UPDATE users SET email = 'tenant@example.ru' WHERE id = ?", (ids["tenant"],)
        )
        db.commit()
        payments = send_payment_reminders()
        assert payments["sent"] == 1
        readings = send_reading_reminders()
        assert readings["sent"] == 1

        # Показание за текущий месяц уже есть — напоминание не отправляется.
        from datetime import date

        save_reading(
            ids["prop1"], ids["tenant"], date.today().replace(day=1).isoformat(),
            {"hot_water": 200, "cool_water": 300, "electricity": 400},
        )
        db.commit()
        assert send_reading_reminders()["sent"] == 0
        assert invoice_id is not None
