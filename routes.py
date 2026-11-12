"""Основные маршруты приложения."""
from __future__ import annotations

import csv
import io
from datetime import date

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from extensions import get_db
from models import (
    add_invoice_payment,
    add_tariff,
    archive_property,
    assign_tenant,
    create_property,
    delete_invoice_payment,
    delete_reading,
    get_archived_properties,
    get_audit_actions,
    get_audit_log,
    get_dashboard_properties,
    get_invoice,
    get_invoice_details,
    get_invoice_payments,
    get_invoice_split,
    get_invoices,
    get_latest_tariff,
    get_property,
    get_property_report_rows,
    get_property_tenants,
    get_stats_data,
    get_tariff_history,
    get_user_debt_summary,
    get_user_properties,
    join_by_invite,
    leave_property,
    mark_invoice_paid,
    mark_invoice_unpaid,
    recalculate_property_invoices,
    regenerate_invite_code,
    remove_tenant,
    save_reading,
    unarchive_property,
    update_property_address,
    update_tenant_share,
    user_can_access_property,
)
from utils import role_required

main_bp = Blueprint("main", __name__)


# ---------------------------------------------------------------------------
# Дашборд
# ---------------------------------------------------------------------------
@main_bp.route("/")
def home():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    return redirect(url_for("auth.login"))


@main_bp.route("/dashboard")
@login_required
def dashboard():
    properties = get_dashboard_properties(current_user)
    summary = get_user_debt_summary(current_user)
    total_last_invoice = sum(
        (p["last_invoice_total"] or 0) for p in properties
    )
    archived_count = 0
    if current_user.is_owner:
        archived_count = len(get_archived_properties(current_user.id))
    return render_template(
        "dashboard.html",
        properties=properties,
        summary=summary,
        total_last_invoice=total_last_invoice,
        archived_count=archived_count,
    )


@main_bp.route("/healthz")
def healthz():
    get_db().execute("SELECT 1").fetchone()
    return jsonify(status="ok", app="HAU_services")


@main_bp.route("/sw.js")
def service_worker():
    response = current_app.send_static_file("sw.js")
    response.headers["Service-Worker-Allowed"] = "/"
    response.headers["Cache-Control"] = "no-cache"
    return response


# ---------------------------------------------------------------------------
# Объекты и тарифы
# ---------------------------------------------------------------------------
@main_bp.route("/properties/new", methods=["GET", "POST"])
@role_required("owner")
def add_property():
    if request.method == "POST":
        try:
            property_id = create_property(
                current_user.id,
                request.form.get("address", ""),
                request.form.get("effective_from") or date.today().isoformat(),
                request.form,
            )
            get_db().commit()
            flash("Объект добавлен. Тарифы сохранены.", "success")
            return redirect(url_for("main.property_tariffs", property_id=property_id))
        except ValueError as exc:
            get_db().rollback()
            flash(str(exc), "danger")
            return render_template("property_form.html", mode="create"), 400
    return render_template("property_form.html", mode="create")


@main_bp.route("/properties/<int:property_id>/edit", methods=["GET", "POST"])
@role_required("owner")
def edit_property(property_id: int):
    prop = get_property(property_id)
    if not prop or prop["owner_id"] != current_user.id:
        flash("Объект не найден или у вас нет доступа.", "danger")
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        try:
            update_property_address(property_id, current_user.id,
                                    request.form.get("address", ""))
            get_db().commit()
            flash("Адрес объекта обновлён.", "success")
            return redirect(url_for("main.dashboard"))
        except ValueError as exc:
            get_db().rollback()
            flash(str(exc), "danger")
            return render_template("property_form.html", mode="edit", prop=prop), 400
    return render_template("property_form.html", mode="edit", prop=prop)


@main_bp.route("/properties/<int:property_id>/tariffs", methods=["GET", "POST"])
@role_required("owner")
def property_tariffs(property_id: int):
    prop = get_property(property_id)
    if not prop or prop["owner_id"] != current_user.id:
        flash("Объект не найден или у вас нет доступа.", "danger")
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        try:
            add_tariff(
                property_id,
                request.form.get("effective_from") or date.today().isoformat(),
                request.form,
                current_user.id,
            )
            recalculated = recalculate_property_invoices(property_id, current_user.id)
            get_db().commit()
            if recalculated:
                flash(
                    f"Тариф сохранён. Пересчитано счетов: {recalculated}.",
                    "success",
                )
            else:
                flash("Новая версия тарифа сохранена. Она применяется к показаниям с указанной даты.", "success")
            return redirect(url_for("main.property_tariffs", property_id=property_id))
        except ValueError as exc:
            get_db().rollback()
            flash(str(exc), "danger")
            return render_template(
                "tariffs.html", prop=prop, tariffs=get_tariff_history(property_id),
                tenants=get_property_tenants(property_id),
            ), 400
    return render_template(
        "tariffs.html", prop=prop, tariffs=get_tariff_history(property_id),
        tenants=get_property_tenants(property_id),
    )


@main_bp.route("/properties/<int:property_id>/invite", methods=["POST"])
@role_required("owner")
def regenerate_invite(property_id: int):
    prop = get_property(property_id)
    if not prop or prop["owner_id"] != current_user.id:
        flash("Объект не найден или у вас нет доступа.", "danger")
        return redirect(url_for("main.dashboard"))
    code = regenerate_invite_code(property_id, current_user.id)
    get_db().commit()
    flash(f"Новый код приглашения: {code}", "success")
    return redirect(url_for("main.property_tariffs", property_id=property_id))


@main_bp.route("/properties/<int:property_id>/archive", methods=["POST"])
@role_required("owner")
def archive_property_route(property_id: int):
    try:
        archive_property(property_id, current_user.id)
        get_db().commit()
        flash("Объект архивирован. История и счета сохранены.", "info")
    except ValueError as exc:
        get_db().rollback()
        flash(str(exc), "danger")
    return redirect(url_for("main.dashboard"))


@main_bp.route("/properties/<int:property_id>/unarchive", methods=["POST"])
@role_required("owner")
def unarchive_property_route(property_id: int):
    try:
        unarchive_property(property_id, current_user.id)
        get_db().commit()
        flash("Объект восстановлен из архива.", "success")
    except ValueError as exc:
        get_db().rollback()
        flash(str(exc), "danger")
    return redirect(url_for("main.archived_properties"))


@main_bp.route("/properties/archived")
@role_required("owner")
def archived_properties():
    return render_template(
        "archived.html",
        properties=get_archived_properties(current_user.id),
    )


# ---------------------------------------------------------------------------
# Арендаторы
# ---------------------------------------------------------------------------
@main_bp.route("/tenants")
@role_required("owner")
def tenants():
    properties = get_user_properties(current_user)
    rows = []
    for prop in properties:
        tenant_rows = get_property_tenants(prop["id"])
        rows.append({
            **prop,
            "tenants": tenant_rows,
            "shares_sum": round(sum(float(t["share"] or 0) for t in tenant_rows), 4),
        })
    return render_template("tenants.html", properties=rows)


@main_bp.route("/tenants/assign", methods=["POST"])
@role_required("owner")
def assign_tenant_route():
    property_id = request.form.get("property_id", type=int)
    username = request.form.get("username", "").strip()
    share = request.form.get("share", "").strip() or None
    try:
        if not property_id:
            raise ValueError("Выберите объект.")
        tenant = assign_tenant(property_id, username, current_user.id, share)
        get_db().commit()
        flash(f"Арендатор {tenant['username']} привязан к объекту.", "success")
    except ValueError as exc:
        get_db().rollback()
        flash(str(exc), "danger")
    return redirect(url_for("main.tenants"))


@main_bp.route("/tenants/<int:property_id>/<int:tenant_id>/share", methods=["POST"])
@role_required("owner")
def update_tenant_share_route(property_id: int, tenant_id: int):
    try:
        update_tenant_share(property_id, tenant_id, current_user.id,
                            request.form.get("share", ""))
        get_db().commit()
        flash("Доля арендатора обновлена.", "success")
    except ValueError as exc:
        get_db().rollback()
        flash(str(exc), "danger")
    return redirect(url_for("main.tenants"))


@main_bp.route("/tenants/<int:property_id>/<int:tenant_id>/remove", methods=["POST"])
@role_required("owner")
def remove_tenant_route(property_id: int, tenant_id: int):
    try:
        remove_tenant(property_id, tenant_id, current_user.id)
        get_db().commit()
        flash("Арендатор отвязан от объекта.", "success")
    except ValueError as exc:
        get_db().rollback()
        flash(str(exc), "danger")
    return redirect(url_for("main.tenants"))


@main_bp.route("/join", methods=["GET", "POST"])
@role_required("tenant")
def join_property():
    if request.method == "POST":
        try:
            prop = join_by_invite(current_user.id, request.form.get("invite_code", ""))
            get_db().commit()
            flash(f"Вы присоединены к объекту «{prop['address']}».", "success")
            return redirect(url_for("main.dashboard"))
        except ValueError as exc:
            get_db().rollback()
            flash(str(exc), "danger")
            return render_template("join.html", properties=get_user_properties(current_user)), 400
    return render_template("join.html", properties=get_user_properties(current_user))


@main_bp.route("/leave/<int:property_id>", methods=["POST"])
@role_required("tenant")
def leave_property_route(property_id: int):
    if not user_can_access_property(current_user, property_id):
        flash("Объект не найден.", "danger")
        return redirect(url_for("main.dashboard"))
    leave_property(current_user.id, property_id)
    get_db().commit()
    flash("Вы отвязались от объекта.", "info")
    return redirect(url_for("main.dashboard"))


# ---------------------------------------------------------------------------
# Показания
# ---------------------------------------------------------------------------
@main_bp.route("/readings/new", methods=["GET", "POST"])
@login_required
def add_reading():
    properties = get_user_properties(current_user)
    if not properties:
        flash("У вас нет доступных объектов для ввода показаний.", "warning")
        return redirect(url_for("main.dashboard"))

    selected = request.args.get("property_id", type=int) or properties[0]["id"]
    if request.method == "POST":
        property_id = request.form.get("property_id", type=int)
        if property_id not in [p["id"] for p in properties]:
            flash("Нет доступа к выбранному объекту.", "danger")
            return render_template("reading_form.html", properties=properties,
                                   selected=selected, mode="create"), 403
        try:
            reading_id = save_reading(
                property_id,
                current_user.id,
                request.form.get("reading_date") or date.today().isoformat(),
                request.form,
            )
            get_db().commit()
            invoice = get_db().execute(
                "SELECT id FROM invoices WHERE reading_id = ?", (reading_id,)
            ).fetchone()
            flash("Показания сохранены, счёт рассчитан.", "success")
            if invoice:
                return redirect(url_for("main.invoice_detail", invoice_id=invoice["id"]))
            return redirect(url_for("main.history", property_id=property_id))
        except ValueError as exc:
            get_db().rollback()
            flash(str(exc), "danger")
            return render_template("reading_form.html", properties=properties,
                                   selected=property_id, mode="create"), 400
    return render_template("reading_form.html", properties=properties,
                           selected=selected, mode="create")


def _can_manage_reading(reading: dict) -> bool:
    prop = get_property(reading["property_id"])
    if not prop:
        return False
    if current_user.is_owner:
        return prop["owner_id"] == current_user.id
    return (reading["user_id"] == current_user.id
            and user_can_access_property(current_user, reading["property_id"]))


@main_bp.route("/readings/<int:reading_id>/edit", methods=["GET", "POST"])
@login_required
def edit_reading(reading_id: int):
    reading = get_db().execute(
        "SELECT * FROM meter_readings WHERE id = ?", (reading_id,)
    ).fetchone()
    if not reading:
        abort(404)
    reading = dict(reading)
    if not _can_manage_reading(reading):
        flash("Нет доступа к редактированию этого показания.", "danger")
        return redirect(url_for("main.dashboard"))
    prop = get_property(reading["property_id"])
    if request.method == "POST":
        try:
            save_reading(
                reading["property_id"],
                reading["user_id"],
                request.form.get("reading_date") or reading["reading_date"],
                request.form,
                reading_id=reading_id,
            )
            get_db().commit()
            flash("Показание обновлено, счета пересчитаны.", "success")
            return redirect(url_for("main.history", property_id=reading["property_id"]))
        except ValueError as exc:
            get_db().rollback()
            flash(str(exc), "danger")
            return render_template("reading_form.html", properties=[prop],
                                   selected=reading["property_id"], mode="edit",
                                   reading=reading), 400
    return render_template("reading_form.html", properties=[prop],
                           selected=reading["property_id"], mode="edit",
                           reading=reading)


@main_bp.route("/readings/<int:reading_id>/delete", methods=["POST"])
@login_required
def delete_reading_route(reading_id: int):
    try:
        reading = get_db().execute(
            "SELECT * FROM meter_readings WHERE id = ?", (reading_id,)
        ).fetchone()
        if not reading:
            raise ValueError("Показание не найдено.")
        reading = dict(reading)
        if not _can_manage_reading(reading):
            raise PermissionError("Нет доступа к удалению этого показания.")
        property_id = delete_reading(reading_id, current_user.id)
        get_db().commit()
        flash("Показание удалено, счета пересчитаны.", "success")
        return redirect(url_for("main.history", property_id=property_id))
    except PermissionError as exc:
        get_db().rollback()
        flash(str(exc), "danger")
        return redirect(url_for("main.dashboard"))
    except ValueError as exc:
        get_db().rollback()
        flash(str(exc), "danger")
        return redirect(url_for("main.dashboard"))


# ---------------------------------------------------------------------------
# История, счета, оплата
# ---------------------------------------------------------------------------
def _get_accessible_property_or_404(property_id: int) -> dict:
    prop = get_property(property_id)
    if not prop or not user_can_access_property(current_user, property_id):
        abort(404)
    return prop


def _history_filters() -> dict:
    status = request.args.get("status", "").strip()
    if status not in ("", "paid", "unpaid", "partial"):
        status = ""
    return {
        "date_from": request.args.get("date_from", "").strip(),
        "date_to": request.args.get("date_to", "").strip(),
        "status": status,
    }


@main_bp.route("/properties/<int:property_id>/history")
@login_required
def history(property_id: int):
    prop = _get_accessible_property_or_404(property_id)
    page = request.args.get("page", 1, type=int)
    filters = _history_filters()
    try:
        invoices = get_invoices(
            property_id,
            page=page,
            date_from=filters["date_from"] or None,
            date_to=filters["date_to"] or None,
            status=filters["status"] or None,
        )
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("main.history", property_id=property_id))
    filter_args = {key: value for key, value in filters.items() if value}
    return render_template(
        "history.html",
        prop=prop,
        invoices=invoices,
        tariff=get_latest_tariff(property_id),
        filters=filters,
        filter_args=filter_args,
    )


@main_bp.route("/properties/<int:property_id>/history.csv")
@login_required
def history_csv(property_id: int):
    _get_accessible_property_or_404(property_id)
    rows = get_property_report_rows(property_id)
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output, delimiter=";", lineterminator="\r\n")
    writer.writerow([
        "Дата", "ГВ, м3", "ХВ, м3", "Электроэнергия, кВт·ч",
        "Расход ГВ, м3", "Расход ХВ, м3", "Расход эл-ва, кВт·ч",
        "Сумма, руб.", "Оплачено", "Оплачено, руб.", "Дата оплаты", "Внёс",
    ])
    for row in rows:
        writer.writerow([
            row["reading_date"], row["hot_water"], row["cool_water"],
            row["electricity"], row["consumption_hot"],
            row["consumption_cold"], row["consumption_el"],
            f"{row['total']:.2f}" if row["total"] is not None else "",
            "Да" if row["paid"] else "Нет",
            f"{row['paid_amount']:.2f}" if row["paid_amount"] is not None else "",
            row["paid_at"] or "",
            row["entered_by"],
        ])
    filename = f"history_property_{property_id}.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@main_bp.route("/invoices/<int:invoice_id>")
@login_required
def invoice_detail(invoice_id: int):
    invoice = get_invoice(invoice_id)
    if not invoice or not user_can_access_property(current_user, invoice["property_id"]):
        abort(404)
    return render_template(
        "invoice_detail.html",
        invoice=invoice,
        details=get_invoice_details(invoice),
        payments=get_invoice_payments(invoice_id),
        split=get_invoice_split(invoice),
        can_pay=current_user.is_owner and invoice["owner_id"] == current_user.id,
        today_value=date.today().isoformat(),
    )


@main_bp.route("/invoices/<int:invoice_id>/print")
@login_required
def invoice_print(invoice_id: int):
    invoice = get_invoice(invoice_id)
    if not invoice or not user_can_access_property(current_user, invoice["property_id"]):
        abort(404)
    return render_template(
        "invoice_print.html",
        invoice=invoice,
        details=get_invoice_details(invoice),
        payments=get_invoice_payments(invoice_id),
        split=get_invoice_split(invoice),
    )


@main_bp.route("/invoices/<int:invoice_id>/pay", methods=["POST"])
@role_required("owner")
def pay_invoice(invoice_id: int):
    try:
        mark_invoice_paid(invoice_id, current_user.id)
        get_db().commit()
        flash("Счёт отмечен как оплаченный.", "success")
        return redirect(url_for("main.invoice_detail", invoice_id=invoice_id))
    except PermissionError as exc:
        get_db().rollback()
        flash(str(exc), "danger")
        return redirect(url_for("main.dashboard"))
    except ValueError as exc:
        get_db().rollback()
        flash(str(exc), "danger")
        return redirect(url_for("main.invoice_detail", invoice_id=invoice_id))


@main_bp.route("/invoices/<int:invoice_id>/unpay", methods=["POST"])
@role_required("owner")
def unpay_invoice(invoice_id: int):
    try:
        mark_invoice_unpaid(invoice_id, current_user.id)
        get_db().commit()
        flash("Оплата счёта отменена.", "info")
        return redirect(url_for("main.invoice_detail", invoice_id=invoice_id))
    except (PermissionError, ValueError) as exc:
        get_db().rollback()
        flash(str(exc), "danger")
        return redirect(url_for("main.dashboard"))


@main_bp.route("/invoices/<int:invoice_id>/payments", methods=["POST"])
@role_required("owner")
def add_payment_route(invoice_id: int):
    try:
        add_invoice_payment(
            invoice_id,
            current_user.id,
            request.form.get("amount", ""),
            request.form.get("paid_at") or None,
            request.form.get("comment", ""),
        )
        get_db().commit()
        flash("Платёж добавлен.", "success")
    except (PermissionError, ValueError) as exc:
        get_db().rollback()
        flash(str(exc), "danger")
    return redirect(url_for("main.invoice_detail", invoice_id=invoice_id))


@main_bp.route("/payments/<int:payment_id>/delete", methods=["POST"])
@role_required("owner")
def delete_payment_route(payment_id: int):
    try:
        invoice = delete_invoice_payment(payment_id, current_user.id)
        get_db().commit()
        flash("Платёж удалён, счёт снова не оплачен.", "info")
        return redirect(url_for("main.invoice_detail", invoice_id=invoice["id"]))
    except (PermissionError, ValueError) as exc:
        get_db().rollback()
        flash(str(exc), "danger")
        return redirect(url_for("main.dashboard"))


@main_bp.route("/invoices/<int:invoice_id>/notify", methods=["POST"])
@role_required("owner")
def notify_invoice(invoice_id: int):
    invoice = get_invoice(invoice_id)
    if not invoice or invoice["owner_id"] != current_user.id:
        abort(404)
    from notifications import notify_invoice_by_email

    result = notify_invoice_by_email(invoice_id)
    flash(result["message"], "success" if result["sent"] else "warning")
    return redirect(url_for("main.invoice_detail", invoice_id=invoice_id))


# ---------------------------------------------------------------------------
# Журнал действий
# ---------------------------------------------------------------------------
@main_bp.route("/audit")
@role_required("owner")
def audit():
    action = request.args.get("action", "").strip() or None
    return render_template(
        "audit.html",
        entries=get_audit_log(current_user.id, action=action),
        actions=get_audit_actions(current_user.id),
        selected_action=action or "",
    )


# ---------------------------------------------------------------------------
# Статистика
# ---------------------------------------------------------------------------
@main_bp.route("/stats")
@login_required
def stats():
    properties = get_user_properties(current_user)
    if not properties:
        flash("У вас нет объектов для просмотра статистики.", "warning")
        return redirect(url_for("main.dashboard"))
    property_id = request.args.get("property_id", 0, type=int)
    if property_id and property_id not in [p["id"] for p in properties]:
        property_id = 0
    months = request.args.get("months", 6, type=int)
    if months not in (0, 6, 12):
        months = 6
    data = get_stats_data(current_user, property_id, months)
    return render_template(
        "stats.html",
        properties=properties,
        selected_property=property_id,
        selected_months=months,
        data=data,
    )
