"""Фабрика приложения «ЖКУ-Учёт»."""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from flask import Flask, render_template

from config import Config
from extensions import csrf, init_app as init_extensions, login_manager
from models import init_db, load_user
from auth import auth_bp
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

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    Path(app.config["DATABASE"]).parent.mkdir(parents=True, exist_ok=True)
    _configure_logging(app)

    login_manager.init_app(app)
    login_manager.user_loader(load_user)
    csrf.init_app(app)
    init_extensions(app)

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)

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
        from datetime import date
        return {"current_year": 2026, "today_iso": date.today().isoformat()}

    @app.errorhandler(403)
    def forbidden(error):  # noqa: ARG001
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(error):  # noqa: ARG001
        return render_template("errors/404.html"), 404

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
