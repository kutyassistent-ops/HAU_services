"""Небольшие утилиты: проверка ролей и безопасный редирект."""
from functools import wraps
from urllib.parse import urljoin, urlparse

from flask import flash, redirect, request, url_for
from flask_login import current_user, login_required


def role_required(*roles: str):
    """Декоратор: доступ только пользователям с указанными ролями."""
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapper(*args, **kwargs):
            if current_user.role not in roles:
                flash("Недостаточно прав для выполнения этого действия.", "danger")
                return redirect(url_for("main.dashboard"))
            return view(*args, **kwargs)
        return wrapper
    return decorator


def is_safe_url(target: str | None) -> bool:
    if not target:
        return False
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ("http", "https") and ref_url.netloc == test_url.netloc


def redirect_back(default_endpoint: str = "main.dashboard", **values):
    target = request.form.get("next") or request.args.get("next")
    if is_safe_url(target):
        return redirect(target)
    return redirect(url_for(default_endpoint, **values))
