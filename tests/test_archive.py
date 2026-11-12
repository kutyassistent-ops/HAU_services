"""Тесты архивирования объектов."""
from __future__ import annotations

import pytest

from extensions import get_db
from models import (
    archive_property,
    assign_tenant,
    get_archived_properties,
    get_dashboard_properties,
    get_user_properties,
    join_by_invite,
    load_user,
    unarchive_property,
)
from tests.conftest import login


def test_archive_and_unarchive(app, ids):
    with app.app_context():
        user = load_user(ids["owner"])
        assert [p["id"] for p in get_user_properties(user)] == [ids["prop1"]]

        archive_property(ids["prop1"], ids["owner"])
        get_db().commit()
        assert get_user_properties(user) == []
        assert [p["id"] for p in get_user_properties(user, include_archived=True)] == [ids["prop1"]]
        assert get_dashboard_properties(user) == []
        assert [p["id"] for p in get_archived_properties(ids["owner"])] == [ids["prop1"]]
        assert get_archived_properties(ids["other_owner"]) == []

        unarchive_property(ids["prop1"], ids["owner"])
        get_db().commit()
        assert [p["id"] for p in get_user_properties(user)] == [ids["prop1"]]
        assert get_archived_properties(ids["owner"]) == []


def test_archive_errors(app, ids):
    with app.app_context():
        with pytest.raises(ValueError, match="не найден"):
            archive_property(ids["prop1"], ids["other_owner"])
        with pytest.raises(ValueError, match="не найден"):
            unarchive_property(ids["prop1"], ids["other_owner"])
        with pytest.raises(ValueError, match="не найден"):
            archive_property(99999, ids["owner"])
        with pytest.raises(ValueError, match="не найден"):
            unarchive_property(99999, ids["owner"])


def test_archived_property_rejects_tenants(app, ids):
    with app.app_context():
        archive_property(ids["prop1"], ids["owner"])
        get_db().commit()
        with pytest.raises(ValueError, match="архивному"):
            assign_tenant(ids["prop1"], "tenant2", ids["owner"])
        code = get_db().execute(
            "SELECT invite_code FROM properties WHERE id = ?", (ids["prop1"],)
        ).fetchone()["invite_code"]
        with pytest.raises(ValueError, match="архивирован"):
            join_by_invite(ids["other_tenant"], code)


def test_archive_routes(app, client, ids):
    login(client)
    response = client.post(
        f"/properties/{ids['prop1']}/archive", follow_redirects=True
    )
    assert "Объект архивирован" in response.get_data(as_text=True)
    assert "В архиве: 1" in response.get_data(as_text=True)

    response = client.get("/properties/archived")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Архив объектов" in body
    assert "ул. Тестовая, 1" in body

    # История архивного объекта остаётся доступной.
    assert client.get(f"/properties/{ids['prop1']}/history").status_code == 200

    response = client.post(
        f"/properties/{ids['prop1']}/unarchive", follow_redirects=True
    )
    assert "восстановлен из архива" in response.get_data(as_text=True)

    response = client.post(
        f"/properties/{ids['prop1']}/archive", follow_redirects=True
    )
    assert "Объект архивирован" in response.get_data(as_text=True)


def test_archive_route_errors(app, client, ids):
    login(client, "owner2")
    response = client.post(
        f"/properties/{ids['prop1']}/archive", follow_redirects=True
    )
    assert "не найден" in response.get_data(as_text=True)
    response = client.post(
        f"/properties/{ids['prop1']}/unarchive", follow_redirects=True
    )
    assert "не найден" in response.get_data(as_text=True)


def test_archived_page_empty(client):
    login(client)
    response = client.get("/properties/archived")
    assert response.status_code == 200
    assert "Архив пуст" in response.get_data(as_text=True)
