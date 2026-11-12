"""Тесты страниц объектов, показаний и статистики."""
from __future__ import annotations

from extensions import get_db
from models import hash_password
from tests.conftest import login
from tests.helpers import add_reading

TARIFF_DATA = {
    "tariff_el": "4.00",
    "tariff_water": "40.00",
    "tariff_com1": "30.00",
    "tariff_com2": "20.00",
    "fixed_fee": "10000",
}


def _create_user(app, username, role):
    with app.app_context():
        db = get_db()
        user_id = db.execute(
            "INSERT INTO users (username, password_hash, role, full_name) VALUES (?, ?, ?, ?)",
            (username, hash_password("password123"), role, username),
        ).lastrowid
        db.commit()
    return user_id


def test_home_and_dashboard_redirects(client):
    response = client.get("/")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/auth/login")
    login(client)
    response = client.get("/")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")


def test_property_create_and_edit(app, client, ids):
    login(client)
    response = client.get("/properties/new")
    assert response.status_code == 200
    assert "Новый объект" in response.get_data(as_text=True)

    response = client.post(
        "/properties/new",
        data={"address": "", "effective_from": "2026-05-01", **TARIFF_DATA},
    )
    assert response.status_code == 400
    assert "Адрес объекта обязателен" in response.get_data(as_text=True)

    response = client.post(
        "/properties/new",
        data={"address": "ул. Новая, 3", "effective_from": "2026-05-01", **TARIFF_DATA},
        follow_redirects=True,
    )
    assert "Объект добавлен" in response.get_data(as_text=True)
    with app.app_context():
        db = get_db()
        new_prop = db.execute(
            "SELECT * FROM properties WHERE address = 'ул. Новая, 3'"
        ).fetchone()
        assert new_prop is not None
        tariff = db.execute(
            "SELECT * FROM property_tariffs WHERE property_id = ?", (new_prop["id"],)
        ).fetchone()
        assert tariff["tariff_el"] == 4.0

    prop_id = new_prop["id"]
    response = client.post(
        f"/properties/{prop_id}/edit",
        data={"address": "ул. Обновлённая, 4"},
        follow_redirects=True,
    )
    assert "Адрес объекта обновлён" in response.get_data(as_text=True)
    response = client.post(
        f"/properties/{prop_id}/edit", data={"address": ""}
    )
    assert response.status_code == 400
    assert "Адрес объекта обязателен" in response.get_data(as_text=True)

    login(client, "owner2")
    response = client.get(f"/properties/{prop_id}/edit", follow_redirects=True)
    assert "не найден" in response.get_data(as_text=True)
    response = client.get(f"/properties/{prop_id}/tariffs", follow_redirects=True)
    assert "не найден" in response.get_data(as_text=True)


def test_invite_regeneration(app, client, ids):
    login(client)
    with app.app_context():
        old_code = get_db().execute(
            "SELECT invite_code FROM properties WHERE id = ?", (ids["prop1"],)
        ).fetchone()["invite_code"]
    response = client.post(
        f"/properties/{ids['prop1']}/invite", follow_redirects=True
    )
    assert "Новый код приглашения" in response.get_data(as_text=True)
    with app.app_context():
        new_code = get_db().execute(
            "SELECT invite_code FROM properties WHERE id = ?", (ids["prop1"],)
        ).fetchone()["invite_code"]
    assert new_code != old_code

    login(client, "owner2")
    response = client.post(
        f"/properties/{ids['prop1']}/invite", follow_redirects=True
    )
    assert "не найден" in response.get_data(as_text=True)


def test_reading_pages_and_permissions(app, client, ids):
    login(client)
    response = client.get("/readings/new")
    assert response.status_code == 200
    assert "Ввод показаний" in response.get_data(as_text=True)

    response = client.post(
        "/readings/new",
        data={
            "property_id": ids["prop1"],
            "reading_date": "bad-date",
            "hot_water": "1", "cool_water": "2", "electricity": "3",
        },
    )
    assert response.status_code == 400
    assert "ГГГГ-ММ-ДД" in response.get_data(as_text=True)

    reading_id, _ = add_reading(app, ids["prop1"], ids["tenant"], "2026-05-01")
    response = client.get(f"/readings/{reading_id}/edit")
    assert response.status_code == 200
    assert "Редактирование показания" in response.get_data(as_text=True)
    response = client.post(
        f"/readings/{reading_id}/edit",
        data={"reading_date": "2026-05-02", "hot_water": "101",
              "cool_water": "201", "electricity": "301"},
        follow_redirects=True,
    )
    assert "Показание обновлено" in response.get_data(as_text=True)

    login(client, "tenant2")
    response = client.get(f"/readings/{reading_id}/edit", follow_redirects=True)
    assert "Нет доступа" in response.get_data(as_text=True)
    response = client.post(f"/readings/{reading_id}/delete", follow_redirects=True)
    assert "Нет доступа" in response.get_data(as_text=True)
    assert client.get("/readings/99999/edit").status_code == 404

    login(client)
    response = client.post("/readings/99999/delete", follow_redirects=True)
    assert "не найдено" in response.get_data(as_text=True)


def test_reading_form_without_properties(app, client):
    _create_user(app, "lonely_tenant", "tenant")
    login(client, "lonely_tenant")
    response = client.get("/readings/new", follow_redirects=True)
    assert "нет доступных объектов" in response.get_data(as_text=True)
    response = client.get("/stats", follow_redirects=True)
    assert "нет объектов" in response.get_data(as_text=True)


def test_stats_filters(app, client, ids):
    add_reading(app, ids["prop1"], ids["tenant"], "2026-05-01")
    login(client)
    response = client.get(f"/stats?property_id={ids['prop1']}&months=12")
    assert response.status_code == 200
    assert "Статистика" in response.get_data(as_text=True)
    response = client.get(f"/stats?property_id={ids['prop2']}&months=5")
    assert response.status_code == 200
    assert "Статистика" in response.get_data(as_text=True)
    login(client, "tenant1")
    assert client.get("/stats").status_code == 200


def test_join_page_without_properties(app, client):
    _create_user(app, "lonely_tenant2", "tenant")
    login(client, "lonely_tenant2")
    response = client.get("/join")
    assert response.status_code == 200
    assert "Присоединиться" in response.get_data(as_text=True)


def test_leave_foreign_property(app, client, ids):
    login(client, "tenant2")
    response = client.post(f"/leave/{ids['prop1']}", follow_redirects=True)
    assert "Объект не найден" in response.get_data(as_text=True)
