from extensions import get_db
from models import get_stats_data, load_user
from tests.conftest import login


def _add_reading(app, property_id, user_id, d, hot, cold, el):
    with app.app_context():
        db = get_db()
        db.execute(
            "INSERT INTO meter_readings (property_id, user_id, reading_date, hot_water, cool_water, electricity) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (property_id, user_id, d, hot, cold, el),
        )
        db.commit()
        from models import recalculate_property_invoices
        recalculate_property_invoices(property_id, user_id)
        db.commit()


def test_stats_page_renders(client):
    login(client)
    response = client.get("/stats")
    assert response.status_code == 200
    assert "Статистика" in response.get_data(as_text=True)


def test_stats_fills_missing_months_with_zero(app, ids):
    _add_reading(app, ids["prop1"], ids["tenant"], "2026-05-01", 100, 200, 300)
    _add_reading(app, ids["prop1"], ids["tenant"], "2026-07-01", 110, 220, 350)
    with app.app_context():
        user = load_user(ids["owner"])
        data = get_stats_data(user, None, 0)
    assert "2026-05" in data["labels"]
    assert "2026-06" in data["labels"]
    june = data["labels"].index("2026-06")
    assert data["totals"][june] == 0.0


def test_stats_first_reading_is_baseline(app, ids):
    _add_reading(app, ids["prop1"], ids["tenant"], "2026-05-01", 100, 200, 300)
    _add_reading(app, ids["prop1"], ids["tenant"], "2026-07-01", 110, 220, 350)
    with app.app_context():
        user = load_user(ids["owner"])
        data = get_stats_data(user, None, 0)
    may = data["labels"].index("2026-05")
    july = data["labels"].index("2026-07")
    assert data["hot_water"][may] == 0.0
    assert data["cold_water"][may] == 0.0
    assert data["electricity"][may] == 0.0
    assert data["hot_water"][july] == 10.0
    assert data["cold_water"][july] == 20.0
    assert data["electricity"][july] == 50.0


def test_stats_aggregates_only_accessible_properties(app, ids):
    _add_reading(app, ids["prop1"], ids["tenant"], "2026-05-01", 100, 200, 300)
    _add_reading(app, ids["prop2"], ids["other_tenant"], "2026-05-01", 100, 200, 300)
    with app.app_context():
        owner1 = load_user(ids["owner"])
        data1 = get_stats_data(owner1, None, 0)
        owner2 = load_user(ids["other_owner"])
        data2 = get_stats_data(owner2, None, 0)
    assert data1["summary"]["total_sum"] == 10000.0
    assert data2["summary"]["total_sum"] == 12000.0


def test_csv_export(client, ids):
    login(client, "tenant1")
    response = client.get(f"/properties/{ids['prop1']}/history.csv")
    assert response.status_code == 200
    assert response.mimetype == "text/csv"
    assert "attachment" in response.headers["Content-Disposition"]
    assert "Дата;ГВ" in response.get_data(as_text=True)


def test_invoice_detail_access(app, client, ids):
    login(client, "tenant1")
    response = client.post(
        "/readings/new",
        data={"property_id": ids["prop1"], "reading_date": "2026-05-01",
              "hot_water": "100", "cool_water": "200", "electricity": "300"},
        follow_redirects=True,
    )
    assert "Детализация расчёта" in response.get_data(as_text=True)
    with app.app_context():
        invoice_id = get_db().execute(
            "SELECT id FROM invoices WHERE property_id = ?", (ids["prop1"],)
        ).fetchone()["id"]
    assert client.get(f"/invoices/{invoice_id}").status_code == 200
    login(client, "tenant2")
    assert client.get(f"/invoices/{invoice_id}").status_code == 404
