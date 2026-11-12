"""Тесты тарифов, зон день/ночь и фиксированной платы."""
from __future__ import annotations

import json

import pytest

from extensions import get_db
from models import (
    add_invoice_payment,
    add_tariff,
    calculate_total,
    get_invoice,
    get_invoice_payments,
    has_electricity_zones,
    mark_invoice_paid,
    recalculate_property_invoices,
    save_reading,
)
from tests.conftest import login
from tests.helpers import add_reading


def _zone_tariff(**overrides):
    values = {
        "tariff_el": 4.0, "tariff_water": 40.0, "tariff_com1": 30.0,
        "tariff_com2": 20.0, "fixed_fee": 1000.0,
        "tariff_el_day": 5.0, "tariff_el_night": 2.0,
    }
    values.update(overrides)
    return values


def test_has_electricity_zones():
    assert not has_electricity_zones(None)
    assert not has_electricity_zones({"tariff_el_day": None, "tariff_el_night": 1})
    assert has_electricity_zones(_zone_tariff())


def test_calculate_total_with_zones():
    reading = {
        "hot_water": 12, "cool_water": 20, "electricity": 30,
        "electricity_day": 110, "electricity_night": 70,
    }
    previous = {
        "hot_water": 10, "cool_water": 15, "electricity": 80,
        "electricity_day": 100, "electricity_night": 50,
    }
    result = calculate_total(reading, previous, _zone_tariff())
    assert result["zones_active"] is True
    assert result["consumption"]["electricity_day"] == 10.0
    assert result["consumption"]["electricity_night"] == 20.0
    assert result["consumption"]["electricity"] == 30.0
    assert result["components"]["electricity"] == 90.0


def test_calculate_total_zones_baseline_and_decrease():
    reading = {
        "hot_water": 1, "cool_water": 1, "electricity": 30,
        "electricity_day": 20, "electricity_night": 10,
    }
    baseline = calculate_total(reading, None, _zone_tariff())
    assert baseline["baseline"] is True
    assert baseline["components"]["electricity"] == 0.0
    assert baseline["components"]["fixed_fee"] == 1000.0

    previous = {
        "hot_water": 1, "cool_water": 1, "electricity": 300,
        "electricity_day": 200, "electricity_night": 100,
    }
    with pytest.raises(ValueError, match="не могут быть меньше"):
        calculate_total(reading, previous, _zone_tariff())


def test_calculate_total_without_fixed_fee():
    reading = {"hot_water": 2, "cool_water": 2, "electricity": 2}
    previous = {"hot_water": 1, "cool_water": 1, "electricity": 1}
    result = calculate_total(reading, previous, _zone_tariff(), charge_fixed_fee=False)
    assert result["fixed_fee_charged"] is False
    assert result["components"]["fixed_fee"] == 0.0


def test_zone_reading_saved_and_calculated(app, ids):
    with app.app_context():
        add_tariff(ids["prop1"], "2025-01-01", _zone_tariff(), ids["owner"])
        db = get_db()
        db.commit()
        save_reading(ids["prop1"], ids["tenant"], "2026-05-01",
                     {"hot_water": 100, "cool_water": 200,
                      "electricity_day": 100, "electricity_night": 50})
        save_reading(ids["prop1"], ids["tenant"], "2026-06-01",
                     {"hot_water": 102, "cool_water": 205,
                      "electricity_day": 110, "electricity_night": 70})
        db.commit()
        row = db.execute(
            """
            SELECT r.electricity, r.electricity_day, r.electricity_night, i.details
            FROM meter_readings r JOIN invoices i ON i.reading_id = r.id
            WHERE r.property_id = ? AND r.reading_date = '2026-06-01'
            """,
            (ids["prop1"],),
        ).fetchone()
        details = json.loads(row["details"])
        assert row["electricity"] == 180.0
        assert row["electricity_day"] == 110.0
        assert row["electricity_night"] == 70.0
        assert details["zones_active"] is True
        assert details["components"]["electricity"] == 90.0


def test_fixed_fee_once_per_calendar_month(app, ids):
    first_id, first_invoice = add_reading(
        app, ids["prop1"], ids["tenant"], "2026-05-01", 100, 200, 300
    )
    second_id, second_invoice = add_reading(
        app, ids["prop1"], ids["tenant"], "2026-05-15", 101, 201, 305
    )
    _, june_invoice = add_reading(
        app, ids["prop1"], ids["tenant"], "2026-06-01", 102, 202, 310
    )
    with app.app_context():
        db = get_db()
        details_first = json.loads(
            db.execute("SELECT details FROM invoices WHERE id = ?", (first_invoice,)).fetchone()["details"]
        )
        details_second = json.loads(
            db.execute("SELECT details FROM invoices WHERE id = ?", (second_invoice,)).fetchone()["details"]
        )
        details_june = json.loads(
            db.execute("SELECT details FROM invoices WHERE id = ?", (june_invoice,)).fetchone()["details"]
        )
        assert details_first["components"]["fixed_fee"] == 10000.0
        assert details_second["components"]["fixed_fee"] == 0.0
        assert details_second["fixed_fee_charged"] is False
        assert details_june["components"]["fixed_fee"] == 10000.0
        assert get_invoice(first_invoice)["total"] == 10000.0
        assert get_invoice(second_invoice)["total"] < 10000.0


def test_tariff_recalculation_revokes_payments(app, ids):
    add_reading(app, ids["prop1"], ids["tenant"], "2026-05-01")
    _, invoice_id = add_reading(
        app, ids["prop1"], ids["tenant"], "2026-06-01", 102, 205, 320
    )
    with app.app_context():
        db = get_db()
        mark_invoice_paid(invoice_id, ids["owner"])
        db.commit()
        assert get_invoice(invoice_id)["paid"] == 1

        add_tariff(ids["prop1"], "2026-05-01", {
            "tariff_el": 10, "tariff_water": 50, "tariff_com1": 30,
            "tariff_com2": 20, "fixed_fee": 10000,
        }, ids["owner"])
        recalculate_property_invoices(ids["prop1"], ids["owner"])
        db.commit()
        invoice = get_invoice(invoice_id)
        assert invoice["paid"] == 0
        assert invoice["paid_amount"] == 0
        assert get_invoice_payments(invoice_id) == []
        audit = db.execute(
            "SELECT * FROM audit_log WHERE action = 'invoice.payment_revoked'"
        ).fetchone()
        assert audit is not None


def test_tariff_recalculation_keeps_payment_when_total_unchanged(app, ids):
    _, invoice_id = add_reading(app, ids["prop1"], ids["tenant"], "2026-05-01")
    with app.app_context():
        db = get_db()
        add_invoice_payment(invoice_id, ids["owner"], 1000)
        db.commit()
        add_tariff(ids["prop1"], "2026-05-01", {
            "tariff_el": 4, "tariff_water": 40, "tariff_com1": 30,
            "tariff_com2": 20, "fixed_fee": 10000,
        }, ids["owner"])
        recalculate_property_invoices(ids["prop1"], ids["owner"])
        db.commit()
        invoice = get_invoice(invoice_id)
        assert invoice["paid_amount"] == 1000.0
        assert invoice["paid"] == 0


def test_tariff_route_saves_zones(app, client, ids):
    login(client)
    response = client.post(
        f"/properties/{ids['prop1']}/tariffs",
        data={
            "effective_from": "2026-04-01",
            "tariff_el": "4.50",
            "tariff_water": "45.00",
            "tariff_com1": "33.00",
            "tariff_com2": "23.00",
            "fixed_fee": "11000",
            "tariff_el_day": "5.50",
            "tariff_el_night": "2.50",
        },
        follow_redirects=True,
    )
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Тариф сохранён" in body or "Новая версия тарифа" in body
    with app.app_context():
        row = get_db().execute(
            "SELECT * FROM property_tariffs WHERE property_id = ? AND effective_from = '2026-04-01'",
            (ids["prop1"],),
        ).fetchone()
        assert row["tariff_el_day"] == 5.5
        assert row["tariff_el_night"] == 2.5


def test_tariff_route_validation_error(app, client, ids):
    login(client)
    response = client.post(
        f"/properties/{ids['prop1']}/tariffs",
        data={
            "effective_from": "2026-04-01",
            "tariff_el": "-1",
            "tariff_water": "45.00",
            "tariff_com1": "33.00",
            "tariff_com2": "23.00",
            "fixed_fee": "11000",
        },
    )
    assert response.status_code == 400
    assert "не могут быть отрицательными" in response.get_data(as_text=True)
