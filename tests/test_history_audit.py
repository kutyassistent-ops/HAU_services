"""Тесты истории, фильтров, аудита, печати и ошибок."""
from __future__ import annotations

import pytest

from extensions import get_db
from models import (
    add_invoice_payment,
    get_invoice,
    get_invoices,
    mark_invoice_paid,
)
from tests.conftest import login
from tests.helpers import add_reading


def test_invoice_history_filters(app, ids):
    _, may_invoice = add_reading(app, ids["prop1"], ids["tenant"], "2026-05-01", 100, 200, 300)
    _, june_invoice = add_reading(app, ids["prop1"], ids["tenant"], "2026-06-01", 102, 205, 320)
    _, july_invoice = add_reading(app, ids["prop1"], ids["tenant"], "2026-07-01", 104, 210, 340)
    with app.app_context():
        db = get_db()
        mark_invoice_paid(may_invoice, ids["owner"])
        add_invoice_payment(june_invoice, ids["owner"], 3000)
        db.commit()

        assert get_invoices(ids["prop1"], status="paid")["total"] == 1
        assert get_invoices(ids["prop1"], status="partial")["total"] == 1
        assert get_invoices(ids["prop1"], status="unpaid")["total"] == 1
        assert get_invoices(ids["prop1"], date_from="2026-06-01")["total"] == 2
        assert get_invoices(ids["prop1"], date_to="2026-06-01")["total"] == 2
        assert get_invoices(ids["prop1"], date_from="2026-06-01", date_to="2026-06-30")["total"] == 1
        page = get_invoices(ids["prop1"], page=2, per_page=2)
        assert page["total"] == 3
        assert page["pages"] == 2
        assert len(page["items"]) == 1
        assert get_invoices(ids["prop1"], page=99, per_page=2)["page"] == 2
        assert get_invoices(ids["prop1"], per_page=0)["per_page"] == 1
        assert get_invoices(ids["prop1"], per_page=1000)["per_page"] == 100
        with pytest.raises(ValueError):
            get_invoices(ids["prop1"], date_from="bad-date")

        assert get_invoice(july_invoice)["paid"] == 0


def test_history_page_filters_and_errors(app, client, ids):
    add_reading(app, ids["prop1"], ids["tenant"], "2026-05-01")
    add_reading(app, ids["prop1"], ids["tenant"], "2026-06-01")
    login(client)
    response = client.get(f"/properties/{ids['prop1']}/history?status=paid")
    assert response.status_code == 200
    assert "История показаний" in response.get_data(as_text=True)
    response = client.get(
        f"/properties/{ids['prop1']}/history?date_from=2026-06-01&date_to=2026-06-30&status=unpaid"
    )
    assert response.status_code == 200
    response = client.get(
        f"/properties/{ids['prop1']}/history?date_from=bad-date", follow_redirects=True
    )
    assert "ГГГГ-ММ-ДД" in response.get_data(as_text=True)

    login(client, "tenant2")
    assert client.get(f"/properties/{ids['prop1']}/history").status_code == 404
    assert client.get("/properties/99999/history").status_code == 404


def test_history_csv_export(app, client, ids):
    _, invoice_id = add_reading(app, ids["prop1"], ids["tenant"], "2026-05-01", 100, 200, 300)
    add_reading(app, ids["prop1"], ids["tenant"], "2026-06-01", 102, 205, 350, day=250, night=100)
    with app.app_context():
        db = get_db()
        add_invoice_payment(invoice_id, ids["owner"], 1000, "2026-05-05", "Аванс")
        db.commit()
    login(client)
    response = client.get(f"/properties/{ids['prop1']}/history.csv")
    assert response.status_code == 200
    assert response.mimetype == "text/csv"
    assert "attachment" in response.headers["Content-Disposition"]
    body = response.data.decode("utf-8-sig")
    assert "Дата;ГВ" in body
    assert "2026-05-01" in body
    assert "1000.00" in body
    assert "Аванс" not in body
    assert "День" not in body


def test_invoice_print_and_notify_disabled(app, client, ids):
    _, invoice_id = add_reading(app, ids["prop1"], ids["tenant"], "2026-05-01")
    login(client)
    response = client.get(f"/invoices/{invoice_id}/print")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Квитанция" in body
    assert "Итого к оплате" in body

    response = client.post(f"/invoices/{invoice_id}/notify", follow_redirects=True)
    assert "Email-уведомления отключены" in response.get_data(as_text=True)

    login(client, "tenant2")
    assert client.get(f"/invoices/{invoice_id}/print").status_code == 404
    login(client, "owner2")
    response = client.post(f"/invoices/{invoice_id}/notify", follow_redirects=True)
    assert response.status_code == 404


def test_audit_page_and_filter(app, client, ids):
    add_reading(app, ids["prop1"], ids["tenant"], "2026-05-01")
    login(client)
    response = client.get("/audit")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Журнал действий" in body
    assert "reading.create" in body

    response = client.get("/audit?action=reading.create")
    assert response.status_code == 200
    assert "reading.create" in response.get_data(as_text=True)
    response = client.get("/audit?action=missing")
    assert "Записей пока нет" in response.get_data(as_text=True)


def test_audit_page_forbidden_for_tenant(client):
    login(client, "tenant1")
    response = client.get("/audit", follow_redirects=True)
    assert "Недостаточно прав" in response.get_data(as_text=True)


def test_error_handlers_and_headers(app, client):
    response = client.get("/no-such-page")
    assert response.status_code == 404
    assert "Страница не найдена" in response.get_data(as_text=True)

    app.config["MAX_CONTENT_LENGTH"] = 100
    response = client.post("/auth/login", data={"username": "x" * 500, "password": "y"})
    assert response.status_code == 413
    assert "Файл слишком большой" in response.get_data(as_text=True)

    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json["status"] == "ok"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert "Referrer-Policy" in response.headers
    assert "Permissions-Policy" in response.headers


def test_service_worker_and_manifest(client):
    response = client.get("/sw.js")
    assert response.status_code == 200
    assert response.headers["Service-Worker-Allowed"] == "/"
    assert response.headers["Cache-Control"] == "no-cache"
    manifest = client.get("/static/manifest.webmanifest")
    assert manifest.status_code == 200
    assert "ЖКУ-Учёт" in manifest.get_data(as_text=True)
