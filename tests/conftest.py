import pytest

from app import create_app
from extensions import get_db
from models import create_property, hash_password


@pytest.fixture()
def app(tmp_path):
    db_path = tmp_path / "test.db"
    app = create_app({
        "TESTING": True,
        "DATABASE": str(db_path),
        "SECRET_KEY": "test-secret",
        "WTF_CSRF_ENABLED": False,
        "LOG_FILE": str(tmp_path / "test.log"),
    })
    with app.app_context():
        db = get_db()
        owner_id = db.execute(
            "INSERT INTO users (username, password_hash, role, full_name) VALUES (?, ?, 'owner', ?)",
            ("owner1", hash_password("password123"), "Владелец Один"),
        ).lastrowid
        tenant_id = db.execute(
            "INSERT INTO users (username, password_hash, role, full_name) VALUES (?, ?, 'tenant', ?)",
            ("tenant1", hash_password("password123"), "Арендатор Один"),
        ).lastrowid
        other_owner_id = db.execute(
            "INSERT INTO users (username, password_hash, role, full_name) VALUES (?, ?, 'owner', ?)",
            ("owner2", hash_password("password123"), "Владелец Два"),
        ).lastrowid
        other_tenant_id = db.execute(
            "INSERT INTO users (username, password_hash, role, full_name) VALUES (?, ?, 'tenant', ?)",
            ("tenant2", hash_password("password123"), "Арендатор Два"),
        ).lastrowid
        db.commit()

        prop1 = create_property(
            owner_id, "ул. Тестовая, 1", "2025-01-01",
            {"tariff_el": 4.0, "tariff_water": 40.0,
             "tariff_com1": 30.0, "tariff_com2": 20.0, "fixed_fee": 10000},
        )
        prop2 = create_property(
            other_owner_id, "ул. Вторая, 2", "2025-01-01",
            {"tariff_el": 5.0, "tariff_water": 50.0,
             "tariff_com1": 35.0, "tariff_com2": 25.0, "fixed_fee": 12000},
        )
        db.execute(
            "INSERT INTO tenant_properties (tenant_id, property_id) VALUES (?, ?)",
            (tenant_id, prop1),
        )
        db.execute(
            "INSERT INTO tenant_properties (tenant_id, property_id) VALUES (?, ?)",
            (other_tenant_id, prop2),
        )
        db.commit()
        app.config["TEST_IDS"] = {
            "owner": owner_id,
            "tenant": tenant_id,
            "other_owner": other_owner_id,
            "other_tenant": other_tenant_id,
            "prop1": prop1,
            "prop2": prop2,
        }
    yield app


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def reset_login_attempts():
    """Сбрасывает счётчик неудачных входов между тестами."""
    from auth import _login_attempts

    _login_attempts.clear()
    yield
    _login_attempts.clear()


@pytest.fixture()
def ids(app):
    return app.config["TEST_IDS"]


def login(client, username="owner1", password="password123"):
    client.post("/auth/logout")
    return client.post(
        "/auth/login",
        data={"username": username, "password": password},
        follow_redirects=True,
    )


def logout(client):
    return client.post("/auth/logout", follow_redirects=True)
