"""Email-уведомления о счетах и напоминания.

Модуль намеренно не зависит от тяжёлых библиотек: используется стандартный
``smtplib``. По умолчанию отправка отключена (``NOTIFICATIONS_ENABLED=0``).
"""
from __future__ import annotations

import smtplib
from email.message import EmailMessage

from flask import current_app

from extensions import get_db
from models import get_invoice, get_invoice_details, get_property_tenants, log_audit


def notifications_configured() -> bool:
    return bool(
        current_app.config.get("NOTIFICATIONS_ENABLED")
        and current_app.config.get("SMTP_HOST")
        and current_app.config.get("SMTP_FROM")
    )


def send_email(to_address: str, subject: str, body: str) -> dict:
    """Отправляет письмо. Возвращает ``{sent, message}`` без выброса исключений."""
    if not notifications_configured():
        return {
            "sent": False,
            "message": "Email-уведомления отключены. Задайте NOTIFICATIONS_ENABLED=1 и SMTP-параметры.",
        }
    if not to_address:
        return {"sent": False, "message": "У получателя не указан email."}

    message = EmailMessage()
    message["From"] = current_app.config["SMTP_FROM"]
    message["To"] = to_address
    message["Subject"] = subject
    message.set_content(body)

    try:
        with smtplib.SMTP(
            current_app.config["SMTP_HOST"],
            current_app.config["SMTP_PORT"],
            timeout=current_app.config.get("SMTP_TIMEOUT", 10),
        ) as smtp:
            if current_app.config.get("SMTP_USE_TLS", True):
                smtp.starttls()
            if current_app.config.get("SMTP_USER"):
                smtp.login(
                    current_app.config["SMTP_USER"],
                    current_app.config["SMTP_PASSWORD"],
                )
            smtp.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        current_app.logger.warning("Не удалось отправить письмо на %s: %s", to_address, exc)
        return {"sent": False, "message": f"Не удалось отправить письмо: {exc}"}
    current_app.logger.info("Письмо отправлено на %s", to_address)
    return {"sent": True, "message": f"Письмо отправлено на {to_address}."}


def _invoice_body(invoice: dict, details: dict) -> str:
    lines = [
        f"Счёт за {invoice['reading_date']} по объекту: {invoice['address']}",
        "",
        f"Сумма к оплате: {float(invoice['total']):.2f} ₽",
    ]
    if details.get("consumption"):
        consumption = details["consumption"]
        lines += [
            f"Горячая вода: {consumption.get('hot_water', 0):.3f} м³",
            f"Холодная вода: {consumption.get('cool_water', 0):.3f} м³",
            f"Электроэнергия: {consumption.get('electricity', 0):.3f} кВт·ч",
        ]
    lines += ["", "Это автоматическое сообщение сервиса «ЖКУ-Учёт»."]
    return "\n".join(lines)


def notify_invoice_by_email(invoice_id: int) -> dict:
    """Отправляет счёт арендаторам и владельцу (если указаны email)."""
    invoice = get_invoice(invoice_id)
    if not invoice:
        return {"sent": False, "message": "Счёт не найден."}
    if not notifications_configured():
        return send_email("", "", "")

    recipients: list[str] = []
    owner_row = get_db().execute(
        "SELECT email FROM users WHERE id = ?", (invoice["owner_id"],)
    ).fetchone()
    if owner_row and owner_row["email"]:
        recipients.append(owner_row["email"])
    for tenant in get_property_tenants(invoice["property_id"]):
        if tenant.get("email"):
            recipients.append(tenant["email"])
    recipients = sorted(set(recipients))
    if not recipients:
        return {"sent": False, "message": "У владельца и арендаторов не указан email."}

    details = get_invoice_details(invoice)
    subject = f"Счёт ЖКУ за {invoice['reading_date']} — {invoice['address']}"
    body = _invoice_body(invoice, details)
    sent_count = 0
    last_message = ""
    for address in recipients:
        result = send_email(address, subject, body)
        last_message = result["message"]
        if result["sent"]:
            sent_count += 1
    if sent_count:
        log_audit("invoice.notify", "invoice", invoice_id,
                  {"recipients": recipients, "sent": sent_count}, invoice["owner_id"])
        get_db().commit()
    return {
        "sent": sent_count > 0,
        "message": (
            f"Уведомление отправлено ({sent_count} из {len(recipients)})."
            if sent_count else last_message
        ),
    }


def send_payment_reminders() -> dict:
    """Напоминает арендаторам о непогашенной задолженности."""
    if not notifications_configured():
        return {"sent": 0, "message": "Email-уведомления отключены."}
    rows = get_db().execute(
        """
        SELECT u.email, p.address,
               ROUND(SUM(
                   CASE WHEN i.paid = 0
                        THEN MAX(i.total - COALESCE((
                            SELECT SUM(pm.amount) FROM payments pm
                            WHERE pm.invoice_id = i.id
                        ), 0), 0)
                        ELSE 0 END
               ), 2) AS debt
        FROM tenant_properties tp
        JOIN users u ON u.id = tp.tenant_id
        JOIN properties p ON p.id = tp.property_id
        JOIN invoices i ON i.property_id = p.id
        WHERE p.archived_at IS NULL
        GROUP BY u.id, p.id
        HAVING debt > 0
        """
    ).fetchall()
    sent = 0
    for row in rows:
        if not row["email"]:
            continue
        body = (
            f"Напоминаем о задолженности по объекту «{row['address']}»: "
            f"{float(row['debt']):.2f} ₽.\n\n"
            "Пожалуйста, оплатите счёт или свяжитесь с арендодателем.\n\n"
            "Сервис «ЖКУ-Учёт»."
        )
        if send_email(row["email"], "Напоминание об оплате ЖКУ", body)["sent"]:
            sent += 1
    return {"sent": sent, "message": f"Отправлено напоминаний: {sent}."}


def send_reading_reminders() -> dict:
    """Напоминает арендаторам внести показания за текущий месяц."""
    if not notifications_configured():
        return {"sent": 0, "message": "Email-уведомления отключены."}
    rows = get_db().execute(
        """
        SELECT DISTINCT u.email, p.address
        FROM tenant_properties tp
        JOIN users u ON u.id = tp.tenant_id
        JOIN properties p ON p.id = tp.property_id
        WHERE p.archived_at IS NULL
          AND u.email IS NOT NULL AND u.email <> ''
          AND NOT EXISTS (
              SELECT 1 FROM meter_readings r
              WHERE r.property_id = p.id
                AND substr(r.reading_date, 1, 7) = strftime('%Y-%m', 'now')
          )
        """
    ).fetchall()
    sent = 0
    for row in rows:
        body = (
            f"Напоминаем: по объекту «{row['address']}» ещё не внесены показания "
            "за текущий месяц.\n\nСервис «ЖКУ-Учёт»."
        )
        if send_email(row["email"], "Пора внести показания счётчиков", body)["sent"]:
            sent += 1
    return {"sent": sent, "message": f"Отправлено напоминаний: {sent}."}
