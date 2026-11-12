"""Дополнительные тесты регистрации, входа и профиля."""
from __future__ import annotations

from extensions import get_db
from models import check_password
from tests.conftest import login, logout
from utils import is_safe_url, redirect_back

REGISTER_DATA = {
    "username": "new_owner",
    "password": "password123",
    "password2": "password123",
    "role": "owner",
    "full_name": "Новый Владелец",
    "email": "new@example.ru",
}


def _register(client, **overrides):
    data = dict(REGISTER_DATA)
    data.update(overrides)
    return client.post("/auth/register", data=data, follow_redirects=True)


def test_register_validation_errors(client):
    response = _register(
        client,
        username="ab",
        password="short",
        password2="other",
        role="admin",
        email="bad-email",
        invite_code="ABC",
    )
    assert response.status_code == 400
    body = response.get_data(as_text=True)
    assert "3–32 символа" in body
    assert "не менее 8 символов" in body
    assert "Пароли не совпадают" in body
    assert "корректную роль" in body
    assert "корректный адрес" in body
    assert "только для арендатора" in body


def test_register_owner_and_duplicate(client):
    response = _register(client)
    assert "Регистрация успешна" in response.get_data(as_text=True)
    with client.application.app_context():
        row = get_db().execute(
            "SELECT * FROM users WHERE username = 'new_owner'"
        ).fetchone()
        assert row is not None
        assert row["role"] == "owner"
        assert row["email"] == "new@example.ru"
        assert check_password("password123", row["password_hash"])

    response = _register(client)
    assert response.status_code == 400
    assert "уже существует" in response.get_data(as_text=True)


def test_register_tenant_with_invite_and_invalid_invite(client):
    with client.application.app_context():
        code = get_db().execute(
            "SELECT invite_code FROM properties WHERE owner_id = 1"
        ).fetchone()["invite_code"]
    response = _register(
        client,
        username="new_tenant",
        role="tenant",
        full_name="Новый Арендатор",
        invite_code=code,
    )
    assert "Регистрация успешна" in response.get_data(as_text=True)
    with client.application.app_context():
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE username = 'new_tenant'").fetchone()
        assert user is not None
        link = db.execute(
            "SELECT 1 FROM tenant_properties WHERE tenant_id = ?", (user["id"],)
        ).fetchone()
        assert link is not None

    response = _register(
        client,
        username="bad_invite",
        role="tenant",
        invite_code="NOPE",
    )
    assert response.status_code == 500
    assert "Не удалось завершить регистрацию" in response.get_data(as_text=True)
    with client.application.app_context():
        assert get_db().execute(
            "SELECT 1 FROM users WHERE username = 'bad_invite'"
        ).fetchone() is None


def test_login_errors_success_and_redirects(client, app):
    response = client.post(
        "/auth/login", data={"username": "owner1", "password": "wrong"}, follow_redirects=True
    )
    assert response.status_code == 401
    assert "Неверное имя" in response.get_data(as_text=True)
    response = client.post(
        "/auth/login", data={"username": "missing", "password": "wrong"}, follow_redirects=True
    )
    assert response.status_code == 401

    response = client.post(
        "/auth/login",
        data={"username": "owner1", "password": "password123", "next": "/stats"},
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/stats")

    logout(client)
    response = client.post(
        "/auth/login",
        data={"username": "owner1", "password": "password123", "next": "http://evil.example/"},
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")

    response = client.get("/auth/login")
    assert response.status_code == 302


def test_login_rate_limiting(app, client):
    app.config["MAX_LOGIN_ATTEMPTS"] = 3
    app.config["LOGIN_LOCKOUT_SECONDS"] = 300
    for _ in range(3):
        response = client.post(
            "/auth/login", data={"username": "owner1", "password": "wrong"}
        )
        assert response.status_code == 401
    response = client.post(
        "/auth/login", data={"username": "owner1", "password": "password123"}
    )
    assert response.status_code == 429
    assert "Слишком много неудачных попыток" in response.get_data(as_text=True)


def test_profile_update_and_validation(app, client, ids):
    login(client)
    response = client.get("/auth/profile")
    assert response.status_code == 200
    assert "Профиль" in response.get_data(as_text=True)

    response = client.post(
        "/auth/profile",
        data={"full_name": "Обновлённое Имя", "email": "owner1@example.ru"},
        follow_redirects=True,
    )
    assert "Профиль обновлён" in response.get_data(as_text=True)
    with app.app_context():
        row = get_db().execute("SELECT * FROM users WHERE id = ?", (ids["owner"],)).fetchone()
        assert row["full_name"] == "Обновлённое Имя"
        assert row["email"] == "owner1@example.ru"

    response = client.post(
        "/auth/profile", data={"full_name": "Имя", "email": "bad"}
    )
    assert response.status_code == 400
    assert "корректный адрес" in response.get_data(as_text=True)

    response = client.post(
        "/auth/profile", data={"full_name": "x" * 151, "email": ""}
    )
    assert response.status_code == 400
    assert "слишком длинное" in response.get_data(as_text=True)


def test_change_password_flow(app, client, ids):
    login(client)
    response = client.post(
        "/auth/password",
        data={"current_password": "wrong", "new_password": "short", "new_password2": "other"},
    )
    assert response.status_code == 400
    body = response.get_data(as_text=True)
    assert "Текущий пароль указан неверно" in body
    assert "не менее 8 символов" in body
    assert "не совпадают" in body

    response = client.post(
        "/auth/password",
        data={
            "current_password": "password123",
            "new_password": "newpassword123",
            "new_password2": "newpassword123",
        },
        follow_redirects=True,
    )
    assert "Пароль изменён" in response.get_data(as_text=True)
    with app.app_context():
        row = get_db().execute("SELECT * FROM users WHERE id = ?", (ids["owner"],)).fetchone()
        assert check_password("newpassword123", row["password_hash"])

    logout(client)
    response = client.post(
        "/auth/login",
        data={"username": "owner1", "password": "newpassword123"},
        follow_redirects=True,
    )
    assert "Вы вошли в систему" in response.get_data(as_text=True)


def test_safe_url_and_redirect_back(app):
    with app.test_request_context(base_url="http://localhost/"):
        assert is_safe_url("/dashboard")
        assert is_safe_url("http://localhost/stats")
        assert not is_safe_url("http://evil.example/")
        assert not is_safe_url(None)
        response = redirect_back()
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/dashboard")
        response = redirect_back("main.stats")
        assert response.headers["Location"].endswith("/stats")
