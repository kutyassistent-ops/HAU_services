"""Тесты вспомогательных функций и моделей."""
from __future__ import annotations

from datetime import date

import pytest

from extensions import get_db
from models import (
    User,
    add_invoice_payment,
    add_tariff,
    assign_tenant,
    generate_invite_code,
    get_audit_actions,
    get_audit_log,
    get_dashboard_properties,
    get_invoice_details,
    get_latest_tariff,
    get_previous_reading,
    get_property_report_rows,
    get_reading,
    get_stats_data,
    get_tariff_for_date,
    get_tariff_history,
    get_user_debt_summary,
    get_user_properties,
    load_user,
    load_user_by_api_token,
    log_audit,
    money,
    month_range,
    parse_iso_date,
    quantity,
    unique_invite_code,
    validate_readings,
    validate_tariff_values,
)
from tests.helpers import add_reading


def test_money_and_quantity_rounding():
    assert money("10.005") == 10.01
    assert money(10) == 10.0
    assert quantity("1.2345") == 1.235
    assert quantity(2) == 2.0


def test_parse_iso_date_validation():
    assert parse_iso_date("2026-05-01").isoformat() == "2026-05-01"
    with pytest.raises(ValueError, match="ГГГГ-ММ-ДД"):
        parse_iso_date("01.05.2026")
    with pytest.raises(ValueError):
        parse_iso_date(None)


def test_validate_tariff_values_errors():
    valid = {
        "tariff_el": 4, "tariff_water": 40, "tariff_com1": 30,
        "tariff_com2": 20, "fixed_fee": 10000,
    }
    assert validate_tariff_values(valid)["tariff_el"] == 4.0
    with pytest.raises(ValueError, match="должно быть числом"):
        validate_tariff_values({**valid, "tariff_el": "abc"})
    with pytest.raises(ValueError, match="некорректное значение"):
        validate_tariff_values({**valid, "tariff_el": "nan"})
    with pytest.raises(ValueError, match="не могут быть отрицательными"):
        validate_tariff_values({**valid, "fixed_fee": -1})
    with pytest.raises(ValueError, match="слишком велико"):
        validate_tariff_values({**valid, "tariff_el": 10_000_001})
    with pytest.raises(ValueError, match="оба тарифа"):
        validate_tariff_values({**valid, "tariff_el_day": 5})
    with pytest.raises(ValueError, match="некорректное значение"):
        validate_tariff_values({**valid, "tariff_el_night": "inf"})
    zoned = validate_tariff_values({**valid, "tariff_el_day": 5, "tariff_el_night": 2})
    assert zoned["tariff_el_day"] == 5.0
    assert zoned["tariff_el_night"] == 2.0


def test_validate_readings_errors():
    base = {"hot_water": 1, "cool_water": 2, "electricity": 3}
    assert validate_readings(base)["electricity"] == 3.0
    with pytest.raises(ValueError, match="должны быть числами"):
        validate_readings({**base, "hot_water": "abc"})
    with pytest.raises(ValueError, match="некорректное значение"):
        validate_readings({**base, "cool_water": "nan"})
    with pytest.raises(ValueError, match="отрицательными"):
        validate_readings({**base, "electricity": -1})
    with pytest.raises(ValueError, match="слишком велико"):
        validate_readings({**base, "electricity": 2_000_000_000})
    with pytest.raises(ValueError, match="оба показания"):
        validate_readings({**base, "electricity_day": 1})
    with pytest.raises(ValueError, match="должны быть числами"):
        validate_readings({**base, "electricity_day": "x", "electricity_night": 1})
    with pytest.raises(ValueError, match="некорректное значение"):
        validate_readings({**base, "electricity_day": "inf", "electricity_night": 1})
    with pytest.raises(ValueError, match="отрицательными"):
        validate_readings({**base, "electricity_day": -1, "electricity_night": 1})
    with pytest.raises(ValueError, match="слишком велико"):
        validate_readings({**base, "electricity_day": 2_000_000_000, "electricity_night": 1})
    zoned = validate_readings({**base, "electricity_day": 1.5, "electricity_night": 2.5})
    assert zoned["electricity"] == 4.0
    assert zoned["electricity_day"] == 1.5
    assert zoned["electricity_night"] == 2.5


def test_invite_codes():
    code = generate_invite_code()
    assert len(code) == 8
    assert code.isalnum()
    with pytest.raises(ValueError, match="оба тарифа"):
        # Проверяет ветку, где заполнена только одна зона.
        validate_tariff_values({
            "tariff_el": 1, "tariff_water": 1, "tariff_com1": 1,
            "tariff_com2": 1, "fixed_fee": 1, "tariff_el_day": "",
            "tariff_el_night": 3,
        })


def test_unique_invite_code_and_tariff_lookup(app, ids):
    with app.app_context():
        assert len(unique_invite_code()) == 8
        assert get_tariff_for_date(ids["prop1"], "2024-01-01") is None
        tariff = get_tariff_for_date(ids["prop1"], "2026-05-01")
        assert tariff["tariff_el"] == 4.0
        assert get_latest_tariff(ids["prop1"])["tariff_el"] == 4.0
        assert len(get_tariff_history(ids["prop1"])) == 1


def test_add_tariff_overwrites_same_date(app, ids):
    with app.app_context():
        add_tariff(ids["prop1"], "2025-01-01", {
            "tariff_el": 9, "tariff_water": 50, "tariff_com1": 30,
            "tariff_com2": 20, "fixed_fee": 10000,
        }, ids["owner"])
        db = get_db()
        db.commit()
        assert get_latest_tariff(ids["prop1"])["tariff_el"] == 9.0
        assert len(get_tariff_history(ids["prop1"])) == 1


def test_user_model_and_api_token(app, ids):
    user = User({
        "id": 1, "username": "u", "role": "owner",
        "full_name": "", "email": None, "created_at": "2026-01-01",
    })
    assert user.is_owner
    assert not user.is_tenant
    assert user.display_name == "u"
    assert user.created_at == "2026-01-01"
    short_row = {"id": 2, "username": "t", "role": "tenant", "full_name": "Т"}
    assert User(short_row).created_at is None

    with app.app_context():
        assert load_user_by_api_token(None) is None
        assert load_user_by_api_token("nope") is None


def test_readings_and_invoices_lookup(app, ids):
    reading_id, invoice_id = add_reading(app, ids["prop1"], ids["tenant"], "2026-05-01")
    with app.app_context():
        reading = get_reading(reading_id)
        assert reading["reading_date"] == "2026-05-01"
        assert get_reading(99999) is None
        assert get_previous_reading(ids["prop1"], "2026-05-01") is None
        assert get_previous_reading(ids["prop1"], "2026-06-01")["id"] == reading_id
        assert get_previous_reading(
            ids["prop1"], "2026-05-01", exclude_id=reading_id
        ) is None
        invoice = get_db().execute(
            "SELECT details FROM invoices WHERE id = ?", (invoice_id,)
        ).fetchone()
        assert get_invoice_details(dict(invoice))["baseline"] is True
        assert get_invoice_details({"details": "not json"}) == {}
        assert get_invoice_details({}) == {}


def test_debt_summary_without_properties(app, ids):
    with app.app_context():
        db = get_db()
        new_id = db.execute(
            "INSERT INTO users (username, password_hash, role) VALUES ('empty_owner', 'x', 'owner')"
        ).lastrowid
        db.commit()
        user = load_user(new_id)
        assert get_user_properties(user) == []
        assert get_user_debt_summary(user) == {"total_debt": 0.0, "properties": 0}


def test_stats_empty_and_selected_property(app, ids):
    with app.app_context():
        owner = load_user(ids["owner"])
        empty = get_stats_data(owner, None, 0)
        assert empty["labels"] == []
        assert empty["summary"]["total_sum"] == 0

    today = date.today().replace(day=1).isoformat()
    add_reading(app, ids["prop1"], ids["tenant"], today, 100, 200, 300)
    with app.app_context():
        owner = load_user(ids["owner"])
        data = get_stats_data(owner, ids["prop1"], 6)
        assert data["labels"]
        assert data["summary"]["months"] == 1
        assert data["totals"][-1] == 10000.0
        assert len(get_stats_data(owner, None, 6)["labels"]) == len(data["labels"])
        missing = get_stats_data(owner, ids["prop2"], 6)
        assert missing["labels"] == []


def test_month_range():
    assert month_range("2026-01", "2026-03") == ["2026-01", "2026-02", "2026-03"]
    assert month_range("2026-03", "2026-01") == []
    assert month_range("2025-11", "2026-02") == [
        "2025-11", "2025-12", "2026-01", "2026-02"
    ]


def test_report_rows_keep_csv_baseline_behaviour(app, ids):
    _, first_invoice = add_reading(app, ids["prop1"], ids["tenant"], "2026-05-01", 100, 200, 300)
    add_reading(app, ids["prop1"], ids["tenant"], "2026-06-01", 102, 205, 320)
    with app.app_context():
        db = get_db()
        add_invoice_payment(first_invoice, ids["owner"], 500)
        db.commit()
        rows = get_property_report_rows(ids["prop1"])
        assert len(rows) == 2
        # Первая строка CSV исторически показывает абсолютное показание как расход.
        assert rows[0]["consumption_hot"] == 100.0
        assert rows[1]["consumption_hot"] == 2.0
        assert rows[0]["paid_amount"] == 500.0
        assert rows[0]["entered_by"] == "tenant1"


def test_audit_helpers(app, ids):
    with app.app_context():
        db = get_db()
        log_audit("test.action", "test", 1, {"a": 1}, ids["owner"])
        log_audit("test.text", "test", 2, "просто текст", ids["owner"])
        log_audit("test.empty", "test", None, None, ids["owner"])
        db.commit()
        entries = get_audit_log(ids["owner"], limit=2)
        assert len(entries) == 2
        assert entries[0]["entity"] == "test"
        assert "test.action" in get_audit_actions(ids["owner"])
        filtered = get_audit_log(ids["owner"], action="test.text")
        assert len(filtered) == 1
        assert filtered[0]["details"] == "просто текст"
        assert get_audit_log(ids["owner"], limit=10000) is not None
        assert get_audit_log(0, action="none") == []


def test_dashboard_for_tenant_and_owner(app, ids):
    add_reading(app, ids["prop1"], ids["tenant"], "2026-05-01")
    with app.app_context():
        owner = load_user(ids["owner"])
        tenant = load_user(ids["tenant"])
        owner_rows = get_dashboard_properties(owner)
        tenant_rows = get_dashboard_properties(tenant)
        assert [row["id"] for row in owner_rows] == [ids["prop1"]]
        assert [row["id"] for row in tenant_rows] == [ids["prop1"]]
        assert owner_rows[0]["last_invoice_total"] == 10000.0
        assert owner_rows[0]["tenant_names"] == "Арендатор Один"


def test_assign_tenant_shares_normalized(app, ids):
    with app.app_context():
        assign_tenant(ids["prop1"], "tenant1", ids["owner"], 0.25)
        row = get_db().execute(
            "SELECT share FROM tenant_properties WHERE property_id = ?", (ids["prop1"],)
        ).fetchone()
        assert row["share"] == 0.25
