import os
from datetime import timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _env_bool(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


class Config:
    """Базовая конфигурация. Значения можно переопределить переменными окружения."""

    APP_VERSION = "2.0.0"
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-secret-key-change-me")
    DATABASE = os.environ.get(
        "DATABASE_PATH", str(BASE_DIR / "instance" / "utility_billing.db")
    )
    HOST = os.environ.get("FLASK_HOST", "0.0.0.0")
    PORT = int(os.environ.get("FLASK_PORT", "5000"))
    MAX_CONTENT_LENGTH = 2 * 1024 * 1024
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _env_bool("SESSION_COOKIE_SECURE")
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE
    REMEMBER_COOKIE_DURATION = timedelta(days=int(os.environ.get("REMEMBER_DAYS", "14")))
    PERMANENT_SESSION_LIFETIME = timedelta(days=int(os.environ.get("SESSION_DAYS", "7")))
    WTF_CSRF_TIME_LIMIT = None
    LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
    LOG_FILE = os.environ.get("LOG_FILE", str(BASE_DIR / "instance" / "app.log"))

    # Защита от перебора пароля (в памяти процесса).
    MAX_LOGIN_ATTEMPTS = int(os.environ.get("MAX_LOGIN_ATTEMPTS", "5"))
    LOGIN_LOCKOUT_SECONDS = int(os.environ.get("LOGIN_LOCKOUT_SECONDS", "300"))

    # Email-уведомления. По умолчанию отключены.
    NOTIFICATIONS_ENABLED = _env_bool("NOTIFICATIONS_ENABLED")
    SMTP_HOST = os.environ.get("SMTP_HOST", "")
    SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
    SMTP_USER = os.environ.get("SMTP_USER", "")
    SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
    SMTP_FROM = os.environ.get("SMTP_FROM", "no-reply@example.ru")
    SMTP_USE_TLS = _env_bool("SMTP_USE_TLS", "1")
    SMTP_TIMEOUT = int(os.environ.get("SMTP_TIMEOUT", "10"))
