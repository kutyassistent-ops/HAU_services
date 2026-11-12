"""Тесты арендаторов, долей и разделения счёта."""
from __future__ import annotations

import pytest

from extensions import get_db
from models import (
    assign_tenant,
    get_invoice,
    get_invoice_split,
    get_property_tenants,
    join_by_invite,
    leave_property,
    load_user,
    remove_tenant,
    update_tenant_share,
)
from tests.conftest import login
from tests.helpers import add_reading


def test_assign_tenants_equal_shares(app, ids):
    with app.app_context():
        assign_tenant(ids["prop1"], "tenant2", ids["owner"])
        get_db().commit()
        tenants = get_property_tenants(ids["prop1"])
        assert [t["username"] for t in tenants] == ["tenant1", "tenant2"]
        assert all(t["share"] == 0.5 for t in tenants)


def test_assign_with_custom_share_and_update(app, ids):
    with app.app_context():
        db = get_db()
        assign_tenant(ids["prop1"], "tenant2", ids["owner"], "0.3")
        db.commit()
        tenants = {t["username"]: t for t in get_property_tenants(ids["prop1"])}
        assert tenants["tenant2"]["share"] == 0.3
        assert tenants["tenant1"]["share"] == 1.0

        update_tenant_share(ids["prop1"], tenants["tenant1"]["id"], ids["owner"], "0.7")
        db.commit()
        tenants = {t["username"]: t for t in get_property_tenants(ids["prop1"])}
        assert tenants["tenant1"]["share"] == 0.7

        unbound_id = db.execute(
            "INSERT INTO users (username, password_hash, role) "
            "VALUES ('unbound_tenant', 'x', 'tenant')"
        ).lastrowid
        db.commit()

        with pytest.raises(ValueError, match="больше 0"):
            update_tenant_share(ids["prop1"], tenants["tenant1"]["id"], ids["owner"], "0")
        with pytest.raises(ValueError, match="больше 0"):
            update_tenant_share(ids["prop1"], tenants["tenant1"]["id"], ids["owner"], "1.5")
        with pytest.raises(ValueError, match="должна быть числом"):
            update_tenant_share(ids["prop1"], tenants["tenant1"]["id"], ids["owner"], "abc")
        with pytest.raises(ValueError, match="не привязан"):
            update_tenant_share(ids["prop1"], unbound_id, ids["owner"], "0.5")
        with pytest.raises(ValueError, match="не найден"):
            update_tenant_share(ids["prop2"], tenants["tenant1"]["id"], ids["owner"], "0.5")


def test_assign_tenant_errors(app, ids):
    with app.app_context():
        with pytest.raises(ValueError, match="не найден"):
            assign_tenant(ids["prop1"], "missing", ids["owner"])
        with pytest.raises(ValueError, match="не найден"):
            assign_tenant(ids["prop1"], "owner2", ids["owner"])
        with pytest.raises(ValueError, match="не найден"):
            assign_tenant(ids["prop2"], "tenant1", ids["owner"])
        with pytest.raises(ValueError, match="должна быть числом"):
            assign_tenant(ids["prop1"], "tenant2", ids["owner"], "abc")
        with pytest.raises(ValueError, match="больше 0"):
            assign_tenant(ids["prop1"], "tenant2", ids["owner"], 0)
        with pytest.raises(ValueError, match="больше 0"):
            assign_tenant(ids["prop1"], "tenant2", ids["owner"], 1.1)


def test_invoice_split_normalizes_shares(app, ids):
    _, invoice_id = add_reading(app, ids["prop1"], ids["tenant"], "2026-05-01")
    with app.app_context():
        db = get_db()
        assign_tenant(ids["prop1"], "tenant2", ids["owner"], "0.3")
        update_tenant_share(ids["prop1"], ids["tenant"], ids["owner"], "0.5")
        db.commit()
        split = get_invoice_split(get_invoice(invoice_id))
        assert split["shares_sum"] == 0.8
        assert len(split["tenants"]) == 2
        assert sum(row["amount"] for row in split["tenants"]) == 10000.0
        by_name = {row["username"]: row["amount"] for row in split["tenants"]}
        assert by_name["tenant1"] == 6250.0
        assert by_name["tenant2"] == 3750.0


def test_invoice_split_without_tenants(app, ids):
    _, invoice_id = add_reading(app, ids["prop2"], ids["other_tenant"], "2026-05-01")
    with app.app_context():
        leave_property(ids["other_tenant"], ids["prop2"])
        get_db().commit()
        split = get_invoice_split(get_invoice(invoice_id))
        assert split == {"tenants": [], "shares_sum": 0.0}


def test_join_by_invite_and_leave(app, ids):
    with app.app_context():
        db = get_db()
        code = db.execute(
            "SELECT invite_code FROM properties WHERE id = ?", (ids["prop1"],)
        ).fetchone()["invite_code"]
        with pytest.raises(ValueError, match="Введите код"):
            join_by_invite(ids["other_tenant"], "  ")
        with pytest.raises(ValueError, match="не найден"):
            join_by_invite(ids["other_tenant"], "BADCODE")
        prop = join_by_invite(ids["other_tenant"], code.lower())
        db.commit()
        assert prop["id"] == ids["prop1"]
        assert [t["username"] for t in get_property_tenants(ids["prop1"])] == [
            "tenant1", "tenant2"
        ]
        assert all(t["share"] == 0.5 for t in get_property_tenants(ids["prop1"]))

        # Повторное присоединение не создаёт дубликат.
        join_by_invite(ids["other_tenant"], code)
        db.commit()
        assert len(get_property_tenants(ids["prop1"])) == 2

        leave_property(ids["other_tenant"], ids["prop1"])
        db.commit()
        assert [t["username"] for t in get_property_tenants(ids["prop1"])] == ["tenant1"]


def test_remove_tenant(app, ids):
    with app.app_context():
        remove_tenant(ids["prop1"], ids["tenant"], ids["owner"])
        get_db().commit()
        assert get_property_tenants(ids["prop1"]) == []
        with pytest.raises(ValueError, match="не найден"):
            remove_tenant(ids["prop2"], ids["tenant"], ids["owner"])


def test_tenants_page_and_routes(app, client, ids):
    login(client)
    response = client.get("/tenants")
    assert response.status_code == 200
    assert "Арендаторы" in response.get_data(as_text=True)
    assert "tenant1" in response.get_data(as_text=True)

    response = client.post(
        "/tenants/assign",
        data={"property_id": ids["prop1"], "username": "tenant2", "share": "0.4"},
        follow_redirects=True,
    )
    assert "привязан" in response.get_data(as_text=True)
    assert "1.40" in response.get_data(as_text=True)

    with app.app_context():
        tenant2 = load_user(ids["other_tenant"])
        assert tenant2.username == "tenant2"
        response = client.post(
            f"/tenants/{ids['prop1']}/{tenant2.id}/share",
            data={"share": "0.6"},
            follow_redirects=True,
        )
        assert "Доля арендатора обновлена" in response.get_data(as_text=True)

        response = client.post(
            f"/tenants/{ids['prop1']}/{tenant2.id}/remove",
            follow_redirects=True,
        )
        assert "отвязан" in response.get_data(as_text=True)


def test_tenant_share_route_validation(app, client, ids):
    login(client)
    response = client.post(
        "/tenants/assign",
        data={"property_id": ids["prop1"], "username": "missing"},
        follow_redirects=True,
    )
    assert "не найден" in response.get_data(as_text=True)
    response = client.post(
        "/tenants/assign",
        data={"property_id": "", "username": "tenant2"},
        follow_redirects=True,
    )
    assert "Выберите объект" in response.get_data(as_text=True)
    response = client.post(
        f"/tenants/{ids['prop1']}/{ids['tenant']}/share",
        data={"share": "abc"},
        follow_redirects=True,
    )
    assert "должна быть числом" in response.get_data(as_text=True)


def test_join_and_leave_routes(app, client, ids):
    login(client, "tenant2")
    response = client.get("/join")
    assert response.status_code == 200
    with app.app_context():
        code = get_db().execute(
            "SELECT invite_code FROM properties WHERE id = ?", (ids["prop1"],)
        ).fetchone()["invite_code"]
    response = client.post(
        "/join", data={"invite_code": code}, follow_redirects=True
    )
    assert "присоединены" in response.get_data(as_text=True)
    response = client.post(
        f"/leave/{ids['prop1']}", follow_redirects=True
    )
    assert "отвязались" in response.get_data(as_text=True)
    response = client.post(
        "/join", data={"invite_code": "NOPE"}, follow_redirects=True
    )
    assert "не найден" in response.get_data(as_text=True)
