"""Тесты read-only REST API и API-токенов."""
from __future__ import annotations

from datetime import date

from extensions import get_db
from models import generate_api_token, load_user
from tests.conftest import login, logout
from tests.helpers import add_reading


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _token(app, user_id: int) -> str:
    with app.app_context():
        token = generate_api_token(user_id)
        get_db().commit()
    return token


def test_api_requires_auth(client):
    assert client.get("/api/v1/me").status_code == 401
    assert client.get("/api/v1/properties").status_code == 401
    assert client.get("/api/v1/me", headers=_headers("bad-token")).status_code == 401


def test_api_me_and_properties(app, client, ids):
    owner_token = _token(app, ids["owner"])
    tenant_token = _token(app, ids["tenant"])

    response = client.get("/api/v1/me", headers=_headers(owner_token))
    assert response.status_code == 200
    assert response.json["username"] == "owner1"
    assert response.json["role"] == "owner"

    response = client.get("/api/v1/properties", headers=_headers(owner_token))
    assert response.status_code == 200
    assert [item["id"] for item in response.json["items"]] == [ids["prop1"]]
    assert response.json["items"][0]["invite_code"]

    response = client.get("/api/v1/properties", headers=_headers(tenant_token))
    assert response.status_code == 200
    assert response.json["items"][0]["invite_code"] is None


def test_api_invoices_access_and_filters(app, client, ids):
    _, first_invoice = add_reading(app, ids["prop1"], ids["tenant"], "2026-05-01")
    _, second_invoice = add_reading(app, ids["prop1"], ids["tenant"], "2026-06-01", 102, 205, 320)
    token = _token(app, ids["owner"])

    assert client.get(
        f"/api/v1/properties/{ids['prop2']}/invoices", headers=_headers(token)
    ).status_code == 404

    response = client.get(
        f"/api/v1/properties/{ids['prop1']}/invoices", headers=_headers(token)
    )
    assert response.status_code == 200
    assert response.json["total"] == 2
    assert response.json["items"][0]["status"] == "unpaid"

    response = client.get(
        f"/api/v1/properties/{ids['prop1']}/invoices?status=paid", headers=_headers(token)
    )
    assert response.json["total"] == 0
    response = client.get(
        f"/api/v1/properties/{ids['prop1']}/invoices?date_from=2026-06-01",
        headers=_headers(token),
    )
    assert response.json["total"] == 1
    response = client.get(
        f"/api/v1/properties/{ids['prop1']}/invoices?date_from=oops",
        headers=_headers(token),
    )
    assert response.status_code == 400
    response = client.get(
        f"/api/v1/properties/{ids['prop1']}/invoices?page=2&per_page=1",
        headers=_headers(token),
    )
    assert response.json["page"] == 2
    assert len(response.json["items"]) == 1

    response = client.get(f"/api/v1/invoices/{first_invoice}", headers=_headers(token))
    assert response.status_code == 200
    assert response.json["details"]["baseline"] is True
    assert response.json["entered_by"] == "Арендатор Один"
    assert client.get(f"/api/v1/invoices/{second_invoice}", headers=_headers(token)).status_code == 200

    other_token = _token(app, ids["other_owner"])
    assert client.get(
        f"/api/v1/invoices/{first_invoice}", headers=_headers(other_token)
    ).status_code == 404
    assert client.get("/api/v1/invoices/99999", headers=_headers(token)).status_code == 404


def test_api_stats(app, client, ids):
    add_reading(
        app, ids["prop1"], ids["tenant"], date.today().replace(day=1).isoformat()
    )
    token = _token(app, ids["owner"])
    response = client.get("/api/v1/stats", headers=_headers(token))
    assert response.status_code == 200
    assert "labels" in response.json
    assert response.json["summary"]["total_sum"] == 10000.0

    response = client.get(
        f"/api/v1/stats?property_id={ids['prop2']}", headers=_headers(token)
    )
    assert response.status_code == 404
    response = client.get(
        f"/api/v1/stats?property_id={ids['prop1']}&months=12", headers=_headers(token)
    )
    assert response.status_code == 200
    assert response.json["summary"]["months"] == 1

    with app.app_context():
        db = get_db()
        new_id = db.execute(
            "INSERT INTO users (username, password_hash, role) VALUES ('api_empty', 'x', 'owner')"
        ).lastrowid
        db.commit()
        empty_user = load_user(new_id)
        assert empty_user is not None
    empty_token = _token(app, new_id)
    assert client.get("/api/v1/stats", headers=_headers(empty_token)).status_code == 404


def test_api_session_auth(app, client):
    login(client)
    response = client.get("/api/v1/me")
    assert response.status_code == 200
    assert response.json["username"] == "owner1"


def test_api_token_routes(app, client):
    login(client)
    response = client.post("/auth/api-token", follow_redirects=True)
    assert response.status_code == 200
    assert "Новый API-токен выпущен" in response.get_data(as_text=True)
    with app.app_context():
        token = get_db().execute(
            "SELECT api_token FROM users WHERE username = 'owner1'"
        ).fetchone()["api_token"]
    assert token
    assert client.get("/api/v1/me", headers=_headers(token)).status_code == 200

    response = client.post("/auth/api-token/revoke", follow_redirects=True)
    assert "API-токен отозван" in response.get_data(as_text=True)
    logout(client)
    assert client.get("/api/v1/me", headers=_headers(token)).status_code == 401
