"""Read-only REST API для мобильного клиента.

Доступен по сессионной cookie (из браузера) или по токену:
``Authorization: Bearer <token>``. Токен выпускается в профиле пользователя.
"""
from __future__ import annotations

from functools import wraps

from flask import Blueprint, g, jsonify, request
from flask_login import current_user

from models import (
    get_invoice,
    get_invoice_details,
    get_invoices,
    get_stats_data,
    get_user_properties,
    load_user_by_api_token,
    user_can_access_property,
)

api_bp = Blueprint("api", __name__, url_prefix="/api/v1")


def _bearer_token() -> str | None:
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return None


def api_login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        user = current_user if current_user.is_authenticated else load_user_by_api_token(_bearer_token())
        if user is None:
            return jsonify(error="Требуется авторизация."), 401
        g.api_user = user
        return view(*args, **kwargs)

    return wrapper


def _invoice_json(invoice: dict) -> dict:
    return {
        "id": invoice["id"],
        "property_id": invoice["property_id"],
        "address": invoice["address"],
        "reading_date": invoice["reading_date"],
        "total": round(float(invoice["total"]), 2),
        "paid": bool(invoice["paid"]),
        "paid_amount": round(float(invoice["paid_amount"]), 2),
        "status": (
            "paid" if invoice["paid"] else
            "partial" if float(invoice["paid_amount"]) > 0 else
            "unpaid"
        ),
        "paid_at": invoice["paid_at"],
    }


@api_bp.route("/me")
@api_login_required
def me():
    user = g.api_user
    return jsonify(
        id=user.id,
        username=user.username,
        full_name=user.full_name,
        role=user.role,
    )


@api_bp.route("/properties")
@api_login_required
def properties():
    user = g.api_user
    return jsonify(
        items=[
            {
                "id": prop["id"],
                "address": prop["address"],
                "invite_code": prop["invite_code"] if user.is_owner else None,
                "created_at": prop["created_at"],
            }
            for prop in get_user_properties(user)
        ]
    )


@api_bp.route("/properties/<int:property_id>/invoices")
@api_login_required
def property_invoices(property_id: int):
    user = g.api_user
    if not user_can_access_property(user, property_id):
        return jsonify(error="Объект не найден."), 404
    status = request.args.get("status", "").strip()
    if status not in ("", "paid", "unpaid", "partial"):
        status = ""
    try:
        result = get_invoices(
            property_id,
            page=request.args.get("page", 1, type=int),
            per_page=request.args.get("per_page", 20, type=int),
            date_from=request.args.get("date_from") or None,
            date_to=request.args.get("date_to") or None,
            status=status or None,
        )
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(
        items=[_invoice_json(item) for item in result["items"]],
        page=result["page"],
        pages=result["pages"],
        total=result["total"],
    )


@api_bp.route("/invoices/<int:invoice_id>")
@api_login_required
def invoice(invoice_id: int):
    row = get_invoice(invoice_id)
    if not row or not user_can_access_property(g.api_user, row["property_id"]):
        return jsonify(error="Счёт не найден."), 404
    payload = _invoice_json(row)
    payload["details"] = get_invoice_details(row)
    payload["entered_by"] = row["entered_by"]
    return jsonify(payload)


@api_bp.route("/stats")
@api_login_required
def stats():
    user = g.api_user
    properties = get_user_properties(user)
    if not properties:
        return jsonify(error="Нет доступных объектов."), 404
    property_id = request.args.get("property_id", 0, type=int)
    if property_id and property_id not in [p["id"] for p in properties]:
        return jsonify(error="Объект не найден."), 404
    months = request.args.get("months", 6, type=int)
    if months not in (0, 6, 12):
        months = 6
    data = get_stats_data(user, property_id, months)
    return jsonify(data)
