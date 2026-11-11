from extensions import get_db
from tests.conftest import login, logout


def test_register_owner(client):
    response = client.post(
        "/auth/register",
        data={
            "username": "new_owner",
            "password": "password123",
            "password2": "password123",
            "role": "owner",
            "full_name": "Новый Владелец",
            "email": "new@example.ru",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Регистрация успешна" in response.get_data(as_text=True)


def test_register_tenant_with_invite(app, client):
    with app.app_context():
        code = get_db().execute(
            "SELECT invite_code FROM properties WHERE id = ?",
            (app.config["TEST_IDS"]["prop1"],),
        ).fetchone()["invite_code"]
    response = client.post(
        "/auth/register",
        data={
            "username": "new_tenant",
            "password": "password123",
            "password2": "password123",
            "role": "tenant",
            "invite_code": code,
        },
        follow_redirects=True,
    )
    assert "Регистрация успешна" in response.get_data(as_text=True)
    with app.app_context():
        tenant = get_db().execute(
            "SELECT id FROM users WHERE username = 'new_tenant'"
        ).fetchone()
        link = get_db().execute(
            "SELECT 1 FROM tenant_properties WHERE tenant_id = ? AND property_id = ?",
            (tenant["id"], app.config["TEST_IDS"]["prop1"]),
        ).fetchone()
        assert link is not None


def test_duplicate_username_rejected(client):
    response = client.post(
        "/auth/register",
        data={
            "username": "owner1",
            "password": "password123",
            "password2": "password123",
            "role": "owner",
        },
        follow_redirects=True,
    )
    assert response.status_code == 400
    assert "уже существует" in response.get_data(as_text=True)


def test_password_mismatch_and_short_password(client):
    response = client.post(
        "/auth/register",
        data={
            "username": "baduser",
            "password": "short",
            "password2": "different",
            "role": "tenant",
        },
    )
    text = response.get_data(as_text=True)
    assert response.status_code == 400
    assert "не совпадают" in text
    assert "не менее 8" in text


def test_login_and_logout(client):
    response = login(client)
    assert response.status_code == 200
    assert "Панель арендодателя" in response.get_data(as_text=True)
    response = logout(client)
    assert "Вы вышли из системы" in response.get_data(as_text=True)


def test_login_with_wrong_password(client):
    response = client.post(
        "/auth/login",
        data={"username": "owner1", "password": "wrong-password"},
    )
    assert response.status_code == 401
    assert "Неверное имя пользователя или пароль" in response.get_data(as_text=True)


def test_dashboard_requires_login(client):
    response = client.get("/dashboard")
    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]


def test_change_password(client):
    login(client)
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
    logout(client)
    assert "Неверное имя пользователя или пароль" in client.post(
        "/auth/login",
        data={"username": "owner1", "password": "password123"},
    ).get_data(as_text=True)
    assert "Вы вошли" in client.post(
        "/auth/login",
        data={"username": "owner1", "password": "newpassword123"},
        follow_redirects=True,
    ).get_data(as_text=True)
