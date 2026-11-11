import json

from extensions import get_db
from models import calculate_total
from tests.conftest import login


def post_reading(client, property_id, reading_date, hot, cold, electricity):
    return client.post(
        "/readings/new",
        data={
            "property_id": property_id,
            "reading_date": reading_date,
            "hot_water": str(hot),
            "cool_water": str(cold),
            "electricity": str(electricity),
        },
        follow_redirects=True,
    )


def test_calculation_formula():
    result = calculate_total(
        {"hot_water": 12, "cool_water": 20, "electricity": 100},
        {"hot_water": 10, "cool_water": 15, "electricity": 80},
        {"tariff_el": 4, "tariff_water": 40, "tariff_com1": 30,
         "tariff_com2": 20, "fixed_fee": 10000},
    )
    assert result["consumption"] == {"hot_water": 2.0, "cool_water": 5.0, "electricity": 20.0}
    assert result["components"]["electricity"] == 80.0
    assert result["components"]["hot_water"] == 80.0
    assert result["components"]["cold_water"] == 200.0
    assert result["components"]["wastewater_cold"] == 150.0
    assert result["components"]["wastewater_total"] == 140.0
    assert result["total"] == 10650.0


def test_first_reading_is_baseline(app, client, ids):
    login(client, "tenant1")
    response = post_reading(client, ids["prop1"], "2026-05-01", 100, 200, 300)
    assert "счёт рассчитан" in response.get_data(as_text=True)
    with app.app_context():
        invoice = get_db().execute(
            "SELECT * FROM invoices WHERE property_id = ?", (ids["prop1"],)
        ).fetchone()
        details = json.loads(invoice["details"])
        assert invoice["total"] == 10000.0
        assert details["baseline"] is True
        assert details["consumption"] == {
            "hot_water": 0.0, "cool_water": 0.0, "electricity": 0.0
        }


def test_second_reading_calculated(app, client, ids):
    login(client, "tenant1")
    post_reading(client, ids["prop1"], "2026-05-01", 100, 200, 300)
    post_reading(client, ids["prop1"], "2026-06-01", 102, 205, 320)
    with app.app_context():
        invoice = get_db().execute(
            "SELECT * FROM invoices WHERE property_id = ? AND reading_id = ("
            "SELECT id FROM meter_readings WHERE property_id = ? AND reading_date = '2026-06-01')",
            (ids["prop1"], ids["prop1"]),
        ).fetchone()
        details = json.loads(invoice["details"])
        assert details["consumption"] == {
            "hot_water": 2.0, "cool_water": 5.0, "electricity": 20.0
        }
        assert invoice["total"] == 10650.0


def test_different_properties_use_their_own_tariffs(app, client, ids):
    with app.app_context():
        db = get_db()
        db.execute(
            "INSERT INTO meter_readings (property_id, user_id, reading_date, hot_water, cool_water, electricity) "
            "VALUES (?, ?, '2026-05-01', 0, 0, 0)",
            (ids["prop1"], ids["tenant"]),
        )
        db.execute(
            "INSERT INTO meter_readings (property_id, user_id, reading_date, hot_water, cool_water, electricity) "
            "VALUES (?, ?, '2026-05-01', 0, 0, 0)",
            (ids["prop2"], ids["other_tenant"]),
        )
        db.commit()
    login(client, "tenant1")
    post_reading(client, ids["prop1"], "2026-06-01", 2, 5, 20)
    login(client, "tenant2")
    post_reading(client, ids["prop2"], "2026-06-01", 2, 5, 20)
    with app.app_context():
        db = get_db()
        first = db.execute(
            "SELECT i.total, i.details FROM invoices i JOIN meter_readings r ON r.id = i.reading_id "
            "WHERE i.property_id = ? AND r.reading_date = '2026-06-01'",
            (ids["prop1"],),
        ).fetchone()
        second = db.execute(
            "SELECT i.total, i.details FROM invoices i JOIN meter_readings r ON r.id = i.reading_id "
            "WHERE i.property_id = ? AND r.reading_date = '2026-06-01'",
            (ids["prop2"],),
        ).fetchone()
        assert first["total"] != second["total"]
        assert json.loads(first["details"])["rates"]["tariff_el"] == 4.0
        assert json.loads(second["details"])["rates"]["tariff_el"] == 5.0


def test_duplicate_date_rejected(app, client, ids):
    login(client, "tenant1")
    post_reading(client, ids["prop1"], "2026-05-01", 100, 200, 300)
    response = post_reading(client, ids["prop1"], "2026-05-01", 101, 201, 301)
    assert response.status_code == 400
    assert "уже введены" in response.get_data(as_text=True)


def test_decreasing_readings_rejected(app, client, ids):
    login(client, "tenant1")
    post_reading(client, ids["prop1"], "2026-05-01", 100, 200, 300)
    response = post_reading(client, ids["prop1"], "2026-06-01", 99, 201, 301)
    assert response.status_code == 400
    assert "не могут быть меньше" in response.get_data(as_text=True)


def test_tariff_version_selected_by_reading_date(app, client, ids):
    login(client)
    client.post(
        f"/properties/{ids['prop1']}/tariffs",
        data={
            "effective_from": "2026-06-01",
            "tariff_el": "5.00",
            "tariff_water": "50.00",
            "tariff_com1": "35.00",
            "tariff_com2": "25.00",
            "fixed_fee": "11000",
        },
    )
    login(client, "tenant1")
    post_reading(client, ids["prop1"], "2026-05-01", 100, 200, 300)
    post_reading(client, ids["prop1"], "2026-06-02", 102, 205, 320)
    with app.app_context():
        invoice = get_db().execute(
            "SELECT i.details FROM invoices i JOIN meter_readings r ON r.id = i.reading_id "
            "WHERE i.property_id = ? AND r.reading_date = '2026-06-02'",
            (ids["prop1"],),
        ).fetchone()
        details = json.loads(invoice["details"])
        assert details["rates"]["tariff_el"] == 5.0
        assert details["tariff_effective_from"] == "2026-06-01"


def test_backdated_reading_recalculates_following_invoice(app, client, ids):
    login(client, "tenant1")
    post_reading(client, ids["prop1"], "2026-05-01", 100, 200, 300)
    post_reading(client, ids["prop1"], "2026-07-01", 110, 220, 350)
    with app.app_context():
        before = get_db().execute(
            "SELECT total FROM invoices i JOIN meter_readings r ON r.id = i.reading_id "
            "WHERE i.property_id = ? AND r.reading_date = '2026-07-01'",
            (ids["prop1"],),
        ).fetchone()["total"]
    post_reading(client, ids["prop1"], "2026-06-01", 105, 210, 325)
    with app.app_context():
        after = get_db().execute(
            "SELECT total FROM invoices i JOIN meter_readings r ON r.id = i.reading_id "
            "WHERE i.property_id = ? AND r.reading_date = '2026-07-01'",
            (ids["prop1"],),
        ).fetchone()["total"]
        assert after < before


def test_edit_and_delete_reading(app, client, ids):
    login(client, "tenant1")
    post_reading(client, ids["prop1"], "2026-05-01", 100, 200, 300)
    post_reading(client, ids["prop1"], "2026-06-01", 102, 205, 320)
    with app.app_context():
        reading = get_db().execute(
            "SELECT id FROM meter_readings WHERE property_id = ? AND reading_date = '2026-06-01'",
            (ids["prop1"],),
        ).fetchone()
    response = client.post(
        f"/readings/{reading['id']}/edit",
        data={
            "reading_date": "2026-06-02",
            "hot_water": "103", "cool_water": "206", "electricity": "321",
        },
        follow_redirects=True,
    )
    assert "Показание обновлено" in response.get_data(as_text=True)
    response = client.post(
        f"/readings/{reading['id']}/delete", follow_redirects=True
    )
    assert "Показание удалено" in response.get_data(as_text=True)
    with app.app_context():
        count = get_db().execute(
            "SELECT COUNT(*) AS c FROM meter_readings WHERE property_id = ?",
            (ids["prop1"],),
        ).fetchone()["c"]
        assert count == 1


def test_tenant_cannot_add_reading_to_foreign_property(client, ids):
    login(client, "tenant1")
    response = post_reading(client, ids["prop2"], "2026-05-01", 1, 2, 3)
    assert response.status_code == 403
    assert "Нет доступа" in response.get_data(as_text=True)


def test_owner_marks_paid_and_unpays(app, client, ids):
    login(client, "tenant1")
    post_reading(client, ids["prop1"], "2026-05-01", 100, 200, 300)
    with app.app_context():
        invoice_id = get_db().execute(
            "SELECT id FROM invoices WHERE property_id = ?", (ids["prop1"],)
        ).fetchone()["id"]
    login(client, "tenant1")
    response = client.post(f"/invoices/{invoice_id}/pay", follow_redirects=True)
    assert "Недостаточно прав" in response.get_data(as_text=True)
    login(client)
    response = client.post(f"/invoices/{invoice_id}/pay", follow_redirects=True)
    assert "отмечен как оплаченный" in response.get_data(as_text=True)
    with app.app_context():
        invoice = get_db().execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
        assert invoice["paid"] == 1
        assert invoice["paid_at"] is not None
        assert invoice["paid_by"] == ids["owner"]
    response = client.post(f"/invoices/{invoice_id}/unpay", follow_redirects=True)
    assert "Оплата счёта отменена" in response.get_data(as_text=True)
    with app.app_context():
        invoice = get_db().execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
        assert invoice["paid"] == 0
