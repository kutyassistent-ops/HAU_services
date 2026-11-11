from extensions import get_db
from tests.conftest import login, logout


def test_owner_creates_property(app, client):
    login(client)
    response = client.post(
        "/properties/new",
        data={
            "address": "ул. Новая, 3",
            "effective_from": "2026-01-01",
            "tariff_el": "6.10",
            "tariff_water": "55.20",
            "tariff_com1": "40.00",
            "tariff_com2": "28.00",
            "fixed_fee": "20000",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Объект добавлен" in response.get_data(as_text=True)
    with app.app_context():
        prop = get_db().execute(
            "SELECT * FROM properties WHERE address = 'ул. Новая, 3'"
        ).fetchone()
        assert prop is not None
        tariff = get_db().execute(
            "SELECT * FROM property_tariffs WHERE property_id = ?",
            (prop["id"],),
        ).fetchone()
        assert tariff["tariff_el"] == 6.10
        assert tariff["fixed_fee"] == 20000


def test_owner_updates_address(app, client, ids):
    login(client)
    response = client.post(
        f"/properties/{ids['prop1']}/edit",
        data={"address": "ул. Тестовая, 10"},
        follow_redirects=True,
    )
    assert "Адрес объекта обновлён" in response.get_data(as_text=True)
    with app.app_context():
        address = get_db().execute(
            "SELECT address FROM properties WHERE id = ?", (ids["prop1"],)
        ).fetchone()["address"]
        assert address == "ул. Тестовая, 10"


def test_owner_adds_tariff_version(app, client, ids):
    login(client)
    response = client.post(
        f"/properties/{ids['prop1']}/tariffs",
        data={
            "effective_from": "2026-06-01",
            "tariff_el": "4.80",
            "tariff_water": "44.00",
            "tariff_com1": "33.00",
            "tariff_com2": "22.00",
            "fixed_fee": "11000",
        },
        follow_redirects=True,
    )
    assert "Новая версия тарифа сохранена" in response.get_data(as_text=True)
    with app.app_context():
        count = get_db().execute(
            "SELECT COUNT(*) AS c FROM property_tariffs WHERE property_id = ?",
            (ids["prop1"],),
        ).fetchone()["c"]
        assert count == 2


def test_tenant_cannot_create_property(client):
    login(client, "tenant1")
    response = client.get("/properties/new", follow_redirects=True)
    assert "Недостаточно прав" in response.get_data(as_text=True)


def test_tenant_cannot_manage_tenants(client):
    login(client, "tenant1")
    response = client.get("/tenants", follow_redirects=True)
    assert "Недостаточно прав" in response.get_data(as_text=True)


def test_invite_code_join(app, client, ids):
    login(client, "tenant2")
    with app.app_context():
        code = get_db().execute(
            "SELECT invite_code FROM properties WHERE id = ?", (ids["prop1"],)
        ).fetchone()["invite_code"]
    response = client.post(
        "/join", data={"invite_code": code}, follow_redirects=True
    )
    assert "Вы присоединены" in response.get_data(as_text=True)
    with app.app_context():
        link = get_db().execute(
            "SELECT 1 FROM tenant_properties WHERE tenant_id = ? AND property_id = ?",
            (ids["other_tenant"], ids["prop1"]),
        ).fetchone()
        assert link is not None


def test_invalid_invite_code(client):
    login(client, "tenant2")
    response = client.post(
        "/join", data={"invite_code": "NOPE0000"}, follow_redirects=True
    )
    assert response.status_code == 400
    assert "Код приглашения не найден" in response.get_data(as_text=True)


def test_owner_assigns_and_removes_tenant(app, client, ids):
    login(client)
    response = client.post(
        "/tenants/assign",
        data={"property_id": ids["prop1"], "username": "tenant2"},
        follow_redirects=True,
    )
    assert "привязан к объекту" in response.get_data(as_text=True)
    with app.app_context():
        link = get_db().execute(
            "SELECT 1 FROM tenant_properties WHERE tenant_id = ? AND property_id = ?",
            (ids["other_tenant"], ids["prop1"]),
        ).fetchone()
        assert link is not None
    response = client.post(
        f"/tenants/{ids['prop1']}/{ids['other_tenant']}/remove",
        follow_redirects=True,
    )
    assert "отвязан от объекта" in response.get_data(as_text=True)
