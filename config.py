import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


class Config:
    """Базовая конфигурация. Значения можно переопределить переменными окружения."""

    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-secret-key-change-me")
    DATABASE = os.environ.get(
        "DATABASE_PATH", str(BASE_DIR / "instance" / "utility_billing.db")
    )
    HOST = os.environ.get("FLASK_HOST", "0.0.0.0")
    PORT = int(os.environ.get("FLASK_PORT", "5000"))
    MAX_CONTENT_LENGTH = 2 * 1024 * 1024
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_HTTPONLY = True
    WTF_CSRF_TIME_LIMIT = None
    LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
    LOG_FILE = os.environ.get("LOG_FILE", str(BASE_DIR / "instance" / "app.log"))
