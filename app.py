"""Фабрика приложения «ЖКУ-Учёт»."""
from __future__ import annotations

import logging
import os
from datetime import date
from logging.handlers import RotatingFileHandler
from pathlib import Path

from flask import Flask, render_template

from api import api_bp
from auth import auth_bp
from config import Config
from extensions import csrf, login_manager
from extensions import init_app as init_extensions
from models import init_db, load_user
from routes import main_bp


def _configure_logging(app: Flask) -> None:
    level = getattr(logging, app.config.get("LOG_LEVEL", "INFO").upper(), logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s"
    )
    log_path = Path(app.config["LOG_FILE"])
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)
    if not any(isinstance(h, RotatingFileHandler) for h in app.logger.handlers):
        app.logger.addHandler(file_handler)
    app.logger.setLevel(level)


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)

    if (app.config["SECRET_KEY"] == "dev-only-secret-key-change-me"
            and not app.config.get("TESTING")):
        app.logger.warning(
            "Используется SECRET_KEY по умолчанию. Задайте переменную окружения "
            "SECRET_KEY перед публикацией приложения."
        )

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    Path(app.config["DATABASE"]).parent.mkdir(parents=True, exist_ok=True)
    _configure_logging(app)

    login_manager.init_app(app)
    login_manager.user_loader(load_user)
    csrf.init_app(app)
    init_extensions(app)

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp)

    @app.template_filter("money")
    def money_filter(value):
        try:
            return f"{float(value):,.2f}".replace(",", " ").replace(".", ",")
        except (TypeError, ValueError):
            return "0,00"

    @app.template_filter("quantity")
    def quantity_filter(value):
        try:
            return f"{float(value):,.3f}".replace(",", " ").replace(".", ",")
        except (TypeError, ValueError):
            return "0,000"

    @app.context_processor
    def inject_globals():
        return {
            "current_year": date.today().year,
            "today_iso": date.today().isoformat(),
            "app_version": app.config.get("APP_VERSION", ""),
        }

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy", "geolocation=(), camera=(), microphone=()"
        )
        return response

    @app.errorhandler(403)
    def forbidden(error):  # noqa: ARG001
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(error):  # noqa: ARG001
        return render_template("errors/404.html"), 404

    @app.errorhandler(413)
    def too_large(error):  # noqa: ARG001
        return render_template("errors/413.html"), 413

    @app.errorhandler(429)
    def too_many_requests(error):  # noqa: ARG001
        return render_template("errors/429.html"), 429

    @app.errorhandler(500)
    def server_error(error):  # noqa: ARG001
        app.logger.exception("Внутренняя ошибка сервера: %s", error)
        return render_template("errors/500.html"), 500

    with app.app_context():
        init_db()
        app.logger.info("База данных инициализирована: %s", app.config["DATABASE"])

    return app


if __name__ == "__main__":
    app = create_app()
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host=app.config["HOST"], port=app.config["PORT"], debug=debug)
