"""Тесты частичных платежей, статусов и долга."""
from __future__ import annotations

import pytest

from extensions import get_db
from models import (
    add_invoice_payment,
    delete_invoice_payment,
    get_dashboard_properties,
    get_invoice,
    get_invoice_payments,
    get_invoices,
    get_user_debt_summary,
    load_user,
    mark_invoice_paid,
    mark_invoice_unpaid,
)
from tests.conftest import login
from tests.helpers import add_reading


def test_partial_payment_then_full(app, ids):
    _, invoice_id = add_reading(app, ids["prop1"], ids["tenant"], "2026-05-01")
    with app.app_context():
        db = get_db()
        invoice = add_invoice_payment(invoice_id, ids["owner"], "4000.00",
                                      "2026-05-10", "Перевод")
        db.commit()
        assert invoice["paid"] == 0
        assert invoice["paid_amount"] == 4000.0

        partial = get_invoices(ids["prop1"], status="partial")
        assert partial["total"] == 1
        assert get_invoices(ids["prop1"], status="unpaid")["total"] == 0
        assert get_invoices(ids["prop1"], status="paid")["total"] == 0

        invoice = add_invoice_payment(invoice_id, ids["owner"], "6000")
        db.commit()
        assert invoice["paid"] == 1
        assert invoice["paid_amount"] == 10000.0
        assert invoice["paid_at"] is not None
        assert get_invoices(ids["prop1"], status="paid")["total"] == 1
        assert get_invoices(ids["prop1"], status="partial")["total"] == 0
        assert len(get_invoice_payments(invoice_id)) == 2


def test_payment_validation(app, ids):
    _, invoice_id = add_reading(app, ids["prop1"], ids["tenant"], "2026-05-01")
    with app.app_context():
        with pytest.raises(ValueError, match="больше нуля"):
            add_invoice_payment(invoice_id, ids["owner"], "0")
        with pytest.raises(ValueError, match="больше нуля"):
            add_invoice_payment(invoice_id, ids["owner"], "-5")
        with pytest.raises(ValueError, match="должна быть числом"):
            add_invoice_payment(invoice_id, ids["owner"], "")
        with pytest.raises(ValueError, match="должна быть числом"):
            add_invoice_payment(invoice_id, ids["owner"], None)
        with pytest.raises(ValueError, match="превышает остаток"):
            add_invoice_payment(invoice_id, ids["owner"], "10001")
        with pytest.raises(PermissionError):
            add_invoice_payment(invoice_id, ids["other_owner"], "100")
        with pytest.raises(ValueError, match="не найден"):
            add_invoice_payment(99999, ids["owner"], "100")

        add_invoice_payment(invoice_id, ids["owner"], "10000")
        with pytest.raises(ValueError, match="уже полностью оплачен"):
            add_invoice_payment(invoice_id, ids["owner"], "1")


def test_delete_payment_reopens_invoice(app, ids):
    _, invoice_id = add_reading(app, ids["prop1"], ids["tenant"], "2026-05-01")
    with app.app_context():
        db = get_db()
        add_invoice_payment(invoice_id, ids["owner"], "3000")
        db.commit()
        payment_id = get_invoice_payments(invoice_id)[0]["id"]

        with pytest.raises(PermissionError):
            delete_invoice_payment(payment_id, ids["other_owner"])
        with pytest.raises(ValueError, match="не найден"):
            delete_invoice_payment(99999, ids["owner"])

        invoice = delete_invoice_payment(payment_id, ids["owner"])
        db.commit()
        assert invoice["paid"] == 0
        assert invoice["paid_amount"] == 0

        add_invoice_payment(invoice_id, ids["owner"], "10000")
        db.commit()
        payment_id = get_invoice_payments(invoice_id)[0]["id"]
        invoice = delete_invoice_payment(payment_id, ids["owner"])
        assert invoice["paid"] == 0
        assert invoice["paid_at"] is None


def test_mark_paid_and_unpaid(app, ids):
    _, invoice_id = add_reading(app, ids["prop1"], ids["tenant"], "2026-05-01")
    with app.app_context():
        db = get_db()
        invoice = mark_invoice_paid(invoice_id, ids["owner"])
        db.commit()
        assert invoice["paid"] == 1
        assert invoice["paid_amount"] == invoice["total"]
        assert len(get_invoice_payments(invoice_id)) == 1
        with pytest.raises(ValueError, match="уже отмечен"):
            mark_invoice_paid(invoice_id, ids["owner"])
        with pytest.raises(PermissionError):
            mark_invoice_paid(invoice_id, ids["other_owner"])
        with pytest.raises(ValueError, match="не найден"):
            mark_invoice_paid(99999, ids["owner"])

        invoice = mark_invoice_unpaid(invoice_id, ids["owner"])
        db.commit()
        assert invoice["paid"] == 0
        assert get_invoice_payments(invoice_id) == []


def test_mark_paid_adds_only_remaining(app, ids):
    _, invoice_id = add_reading(app, ids["prop1"], ids["tenant"], "2026-05-01")
    with app.app_context():
        db = get_db()
        add_invoice_payment(invoice_id, ids["owner"], "4000")
        db.commit()
        invoice = mark_invoice_paid(invoice_id, ids["owner"])
        db.commit()
        payments = get_invoice_payments(invoice_id)
        assert invoice["paid"] == 1
        assert len(payments) == 2
        assert sum(p["amount"] for p in payments) == 10000.0


def test_debt_summary_uses_partial_payments(app, ids):
    _, invoice_id = add_reading(app, ids["prop1"], ids["tenant"], "2026-05-01")
    with app.app_context():
        db = get_db()
        add_invoice_payment(invoice_id, ids["owner"], "2500")
        db.commit()
        user = load_user(ids["owner"])
        summary = get_user_debt_summary(user)
        assert summary["total_debt"] == 7500.0
        props = get_dashboard_properties(user)
        assert props[0]["debt"] == 7500.0
        assert props[0]["last_invoice_paid_amount"] == 2500.0


def test_payment_routes(app, client, ids):
    _, invoice_id = add_reading(app, ids["prop1"], ids["tenant"], "2026-05-01")
    login(client)

    response = client.post(
        f"/invoices/{invoice_id}/payments",
        data={"amount": "4000", "paid_at": "2026-05-10", "comment": "Тест"},
        follow_redirects=True,
    )
    assert "Платёж добавлен" in response.get_data(as_text=True)
    assert "Частично оплачено" in response.get_data(as_text=True)

    with app.app_context():
        payment_id = get_invoice_payments(invoice_id)[0]["id"]
        assert get_invoice(invoice_id)["paid_amount"] == 4000.0

    response = client.post(
        f"/payments/{payment_id}/delete", follow_redirects=True
    )
    assert "Платёж удалён" in response.get_data(as_text=True)
    with app.app_context():
        assert get_invoice(invoice_id)["paid_amount"] == 0.0

    response = client.post(f"/invoices/{invoice_id}/pay", follow_redirects=True)
    assert "отмечен как оплаченный" in response.get_data(as_text=True)
    with app.app_context():
        assert get_invoice(invoice_id)["paid"] == 1

    response = client.post(f"/invoices/{invoice_id}/unpay", follow_redirects=True)
    assert "Оплата счёта отменена" in response.get_data(as_text=True)
    with app.app_context():
        assert get_invoice(invoice_id)["paid"] == 0


def test_payment_route_errors(app, client, ids):
    _, invoice_id = add_reading(app, ids["prop1"], ids["tenant"], "2026-05-01")
    login(client)
    response = client.post(
        f"/invoices/{invoice_id}/payments",
        data={"amount": "999999"},
        follow_redirects=True,
    )
    assert "превышает остаток" in response.get_data(as_text=True)

    login(client, "tenant1")
    response = client.post(
        f"/invoices/{invoice_id}/payments",
        data={"amount": "100"},
        follow_redirects=True,
    )
    assert "Недостаточно прав" in response.get_data(as_text=True)

    login(client, "owner2")
    response = client.post(
        f"/invoices/{invoice_id}/payments",
        data={"amount": "100"},
        follow_redirects=True,
    )
    assert "владельцем" in response.get_data(as_text=True)
