"""WSGI-точка входа для gunicorn и продакшен-серверов."""

from app import create_app

app = create_app()
