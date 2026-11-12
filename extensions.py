"""Расширения Flask и единая точка доступа к SQLite."""
import sqlite3

from flask import current_app, g
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect

login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Пожалуйста, войдите, чтобы продолжить."
login_manager.login_message_category = "warning"

csrf = CSRFProtect()


def get_db() -> sqlite3.Connection:
    """Соединение с БД в рамках текущего запроса/контекста приложения."""
    if "db" not in g:
        g.db = sqlite3.connect(
            current_app.config["DATABASE"],
            timeout=current_app.config.get("SQLITE_TIMEOUT", 10),
        )
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
        g.db.execute("PRAGMA busy_timeout = 5000")
        g.db.execute("PRAGMA journal_mode = WAL")
        g.db.execute("PRAGMA synchronous = NORMAL")
    return g.db


def close_db(exception=None) -> None:  # noqa: ARG001
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_app(app) -> None:
    app.teardown_appcontext(close_db)
