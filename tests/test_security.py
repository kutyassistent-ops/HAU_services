
from app import create_app
from extensions import get_db
from tests.conftest import login


def test_password_is_hashed(app, ids):
    with app.app_context():
        row = get_db().execute(
            "SELECT password_hash FROM users WHERE id = ?", (ids["owner"],)
        ).fetchone()
        assert row["password_hash"] != "password123"
        assert row["password_hash"].startswith("$2")


def test_xss_in_address_is_escaped(app, client, ids):
    with app.app_context():
        get_db().execute(
            "UPDATE properties SET address = ? WHERE id = ?",
            ("<script>alert('xss')</script>", ids["prop1"]),
        )
        get_db().commit()
    login(client)
    response = client.get("/dashboard")
    text = response.get_data(as_text=True)
    assert "<script>alert('xss')</script>" not in text
    assert "&lt;script&gt;" in text


def test_sql_injection_in_login(client):
    response = client.post(
        "/auth/login",
        data={"username": "' OR 1=1 --", "password": "anything"},
    )
    assert response.status_code == 401
    assert "Неверное имя пользователя" in response.get_data(as_text=True)


def test_unknown_property_returns_404(client, ids):
    login(client)
    assert client.get(f"/properties/{ids['prop1'] + 999}/history").status_code == 404


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"


def test_csrf_protection_enabled(tmp_path):
    app = create_app({
        "TESTING": True,
        "DATABASE": str(tmp_path / "csrf.db"),
        "SECRET_KEY": "csrf-test",
        "WTF_CSRF_ENABLED": True,
        "LOG_FILE": str(tmp_path / "csrf.log"),
    })
    client = app.test_client()
    response = client.post(
        "/auth/register",
        data={
            "username": "csrfuser",
            "password": "password123",
            "password2": "password123",
            "role": "owner",
        },
    )
    assert response.status_code == 400
    assert "CSRF" in response.get_data(as_text=True)
