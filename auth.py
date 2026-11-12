"""Регистрация, аутентификация и управление профилем."""
from __future__ import annotations

import re
import threading
import time

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user

from extensions import get_db
from models import (
    User,
    check_password,
    generate_api_token,
    hash_password,
    join_by_invite,
    log_audit,
    revoke_api_token,
)
from utils import is_safe_url

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_login_attempts: dict[str, list[float]] = {}
_login_lock = threading.Lock()


def _login_key(username: str) -> str:
    return f"{request.remote_addr or 'unknown'}:{(username or '').lower()}"


def _is_login_locked(key: str) -> bool:
    window = int(current_app.config.get("LOGIN_LOCKOUT_SECONDS", 300))
    now = time.monotonic()
    with _login_lock:
        attempts = [stamp for stamp in _login_attempts.get(key, []) if now - stamp < window]
        _login_attempts[key] = attempts
        return len(attempts) >= int(current_app.config.get("MAX_LOGIN_ATTEMPTS", 5))


def _record_login_failure(key: str) -> None:
    with _login_lock:
        _login_attempts.setdefault(key, []).append(time.monotonic())


def _clear_login_failures(key: str) -> None:
    with _login_lock:
        _login_attempts.pop(key, None)


def _validate_registration(username: str, password: str, password2: str,
                           role: str, email: str) -> list[str]:
    errors = []
    if not USERNAME_RE.match(username or ""):
        errors.append("Имя пользователя: 3–32 символа, латинские буквы, цифры, точка, дефис или подчёркивание.")
    if len(password or "") < 8:
        errors.append("Пароль должен содержать не менее 8 символов.")
    if password != password2:
        errors.append("Пароли не совпадают.")
    if role not in ("owner", "tenant"):
        errors.append("Выберите корректную роль.")
    if email and not EMAIL_RE.match(email):
        errors.append("Укажите корректный адрес электронной почты.")
    return errors


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        password2 = request.form.get("password2", "")
        role = request.form.get("role", "")
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip()
        invite_code = request.form.get("invite_code", "").strip()

        errors = _validate_registration(username, password, password2, role, email)
        if invite_code and role != "tenant":
            errors.append("Код приглашения предназначен только для арендатора.")

        db = get_db()
        if not errors and db.execute(
            "SELECT 1 FROM users WHERE username = ?", (username,)
        ).fetchone():
            errors.append("Пользователь с таким именем уже существует.")

        if errors:
            for error in errors:
                flash(error, "danger")
            return render_template("register.html"), 400

        try:
            cur = db.execute(
                """
                INSERT INTO users (username, password_hash, role, full_name, email)
                VALUES (?, ?, ?, ?, ?)
                """,
                (username, hash_password(password), role, full_name, email or None),
            )
            user_id = cur.lastrowid
            if invite_code:
                join_by_invite(user_id, invite_code)
            log_audit("user.register", "user", user_id,
                      {"role": role, "invite_code": invite_code or None}, user_id)
            db.commit()
        except Exception:
            db.rollback()
            flash("Не удалось завершить регистрацию. Попробуйте ещё раз.", "danger")
            return render_template("register.html"), 500

        flash("Регистрация успешна. Теперь войдите в систему.", "success")
        return redirect(url_for("auth.login"))

    return render_template("register.html")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        remember = request.form.get("remember") == "on"
        key = _login_key(username)
        if _is_login_locked(key):
            flash(
                "Слишком много неудачных попыток входа. "
                "Подождите несколько минут и попробуйте снова.",
                "danger",
            )
            return render_template("login.html", username=username), 429
        row = get_db().execute(
            """
            SELECT id, username, password_hash, role, full_name, email, created_at
            FROM users WHERE username = ?
            """,
            (username,),
        ).fetchone()
        if row and check_password(password, row["password_hash"]):
            _clear_login_failures(key)
            session.clear()
            login_user(User(row), remember=remember)
            log_audit("user.login", "user", row["id"], None, row["id"])
            get_db().commit()
            flash("Вы вошли в систему.", "success")
            next_url = request.args.get("next") or request.form.get("next")
            if is_safe_url(next_url):
                return redirect(next_url)
            return redirect(url_for("main.dashboard"))
        _record_login_failure(key)
        flash("Неверное имя пользователя или пароль.", "danger")
        return render_template("login.html", username=username), 401

    return render_template("login.html")


@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    log_audit("user.logout", "user", current_user.id, None, current_user.id)
    get_db().commit()
    logout_user()
    flash("Вы вышли из системы.", "info")
    return redirect(url_for("auth.login"))


@auth_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip()
        if len(full_name) > 150:
            flash("Полное имя слишком длинное.", "danger")
            return render_template("profile.html"), 400
        if email and not EMAIL_RE.match(email):
            flash("Укажите корректный адрес электронной почты.", "danger")
            return render_template("profile.html"), 400
        db = get_db()
        db.execute(
            "UPDATE users SET full_name = ?, email = ? WHERE id = ?",
            (full_name, email or None, current_user.id),
        )
        log_audit("user.profile_update", "user", current_user.id, None, current_user.id)
        db.commit()
        flash("Профиль обновлён.", "success")
        return redirect(url_for("auth.profile"))
    row = get_db().execute(
        "SELECT api_token FROM users WHERE id = ?", (current_user.id,)
    ).fetchone()
    return render_template("profile.html", api_token=row["api_token"] if row else None)


@auth_bp.route("/api-token", methods=["POST"])
@login_required
def create_api_token():
    generate_api_token(current_user.id)
    get_db().commit()
    flash("Новый API-токен выпущен. Старый токен перестал действовать.", "success")
    return redirect(url_for("auth.profile"))


@auth_bp.route("/api-token/revoke", methods=["POST"])
@login_required
def delete_api_token():
    revoke_api_token(current_user.id)
    get_db().commit()
    flash("API-токен отозван.", "info")
    return redirect(url_for("auth.profile"))


@auth_bp.route("/password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        new_password2 = request.form.get("new_password2", "")
        db = get_db()
        row = db.execute(
            "SELECT password_hash FROM users WHERE id = ?", (current_user.id,)
        ).fetchone()
        errors = []
        if not row or not check_password(current_password, row["password_hash"]):
            errors.append("Текущий пароль указан неверно.")
        if len(new_password) < 8:
            errors.append("Новый пароль должен содержать не менее 8 символов.")
        if new_password != new_password2:
            errors.append("Новые пароли не совпадают.")
        if errors:
            for error in errors:
                flash(error, "danger")
            return render_template("change_password.html"), 400
        db.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (hash_password(new_password), current_user.id),
        )
        log_audit("user.password_change", "user", current_user.id, None, current_user.id)
        db.commit()
        flash("Пароль изменён.", "success")
        return redirect(url_for("auth.profile"))
    return render_template("change_password.html")
