# routes/admin_reservations.py

from io import BytesIO
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import (
    Blueprint,
    render_template,
    request,
    send_file,
    abort,
    redirect,
    url_for,
    flash,
)
from sqlalchemy import select, desc, func

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib import colors

from db import db
from models import Purchase
from routes.admin_auth import admin_required


bp_admin_reservations = Blueprint("admin_reservations", __name__)

SAO_PAULO_TZ = ZoneInfo("America/Sao_Paulo")


# =========================================================
# Helpers
# =========================================================

def now_sp():
    return datetime.now(SAO_PAULO_TZ).replace(tzinfo=None)


def _safe_filename(s: str) -> str:
    s = (s or "").strip().lower()
    s = "".join(ch if ch.isalnum() else "-" for ch in s)
    while "--" in s:
        s = s.replace("--", "-")
    return s.strip("-") or "show"


EDITABLE_STATUSES = [
    "reservation_pending",
    "reservation_pending_price",
    "reserved",
    "paid",
    "pending_payment",
    "cancelled",
    "rejected",
]


# =========================================================
# LISTAGEM
# =========================================================

@bp_admin_reservations.get("/admin/reservations")
@admin_required
def admin_reservations():
    q = (request.args.get("q") or "").strip().lower()
    show_selected = (request.args.get("show") or "").strip()

    with db() as s:
        show_stats = list(
            s.execute(
                select(
                    Purchase.show_name,
                    func.coalesce(func.sum(Purchase.ticket_qty), 0)
                )
                .where(Purchase.status.in_(["reserved", "paid"]))
                .group_by(Purchase.show_name)
                .order_by(Purchase.show_name.asc())
            ).all()
        )

        show_options = [name for (name, _) in show_stats]

        purchases = list(
            s.scalars(
                select(Purchase)
                .where(Purchase.status.in_(["reserved", "paid"]))
                .order_by(desc(Purchase.created_at))
                .limit(2000)
            )
        )

    rows = []
    total_reservas = 0
    total_pessoas = 0

    for p in purchases:
        if show_selected and (p.show_name or "") != show_selected:
            continue

        hay = " ".join([
            p.buyer_name or "",
            p.buyer_cpf or "",
            p.buyer_cpf_digits or "",
            p.buyer_email or "",
            p.buyer_phone or "",
            p.token or "",
        ]).lower()

        if q and q not in hay:
            continue

        rows.append(p)
        total_reservas += 1
        total_pessoas += int(p.ticket_qty or 1)

    return render_template(
        "admin_reservations.html",
        rows=rows,
        q=q,
        show_options=show_options,
        show_selected=show_selected,
        total_reservas=total_reservas,
        total_pessoas=total_pessoas,
        show_stats=show_stats,
    )


# =========================================================
# EDITAR (GET)
# =========================================================

@bp_admin_reservations.get("/admin/reservations/<int:purchase_id>/edit")
@admin_required
def admin_reservation_edit(purchase_id: int):
    back_show = (request.args.get("show") or "").strip()
    back_q = (request.args.get("q") or "").strip()

    with db() as s:
        p = s.get(Purchase, purchase_id)
        if not p:
            abort(404)

    return render_template(
        "admin_reservation_edit.html",
        p=p,
        statuses=EDITABLE_STATUSES,
        back_show=back_show,
        back_q=back_q,
    )


# =========================================================
# EDITAR (POST)
# =========================================================

@bp_admin_reservations.post("/admin/reservations/<int:purchase_id>/edit")
@admin_required
def admin_reservation_edit_post(purchase_id: int):
    back_show = (request.form.get("back_show") or "").strip()
    back_q = (request.form.get("back_q") or "").strip()

    def _clean(v):
        return (v or "").strip()

    def _to_int(v, default=0, min_value=None):
        try:
            n = int((v or "").strip())
        except ValueError:
            n = default
        if min_value is not None and n < min_value:
            n = min_value
        return n

    with db() as s:
        p = s.get(Purchase, purchase_id)
        if not p:
            abort(404)

        p.buyer_name = _clean(request.form.get("buyer_name")) or p.buyer_name
        p.buyer_email = _clean(request.form.get("buyer_email")) or None
        p.buyer_phone = _clean(request.form.get("buyer_phone")) or None
        p.buyer_cpf = _clean(request.form.get("buyer_cpf")) or None
        p.buyer_cpf_digits = _clean(request.form.get("buyer_cpf_digits")) or None

        p.show_name = _clean(request.form.get("show_name")) or p.show_name
        p.token = _clean(request.form.get("token")) or p.token

        p.ticket_qty = _to_int(request.form.get("ticket_qty"), 1, 1)
        p.qty_adult = _to_int(request.form.get("qty_adult"), 0, 0)
        p.qty_child = _to_int(request.form.get("qty_child"), 0, 0)

        p.guests_text = _clean(request.form.get("guests_text")) or None
        p.rejection_reason = _clean(request.form.get("rejection_reason")) or None

        status = _clean(request.form.get("status"))
        if status in EDITABLE_STATUSES:
            p.status = status

        s.add(p)
        s.commit()

    flash("Reserva atualizada com sucesso.", "success")
    return redirect(url_for("admin_reservations.admin_reservations", show=back_show, q=back_q))


# =========================================================
# EXCLUIR (DELETE FÍSICO)
# =========================================================

@bp_admin_reservations.post("/admin/reservations/<int:purchase_id>/delete")
@admin_required
def admin_reservation_delete(purchase_id: int):
    back_show = (request.form.get("back_show") or "").strip()
    back_q = (request.form.get("back_q") or "").strip()

    with db() as s:
        p = s.get(Purchase, purchase_id)
        if not p:
            abort(404)

        if p.status == "paid":
            flash("Reserva já está como PAID. Use cancelamento ao invés de excluir.", "error")
            return redirect(url_for("admin_reservations.admin_reservations", show=back_show, q=back_q))

        s.delete(p)
        s.commit()

    flash("Reserva excluída.", "success")
    return redirect(url_for("admin_reservations.admin_reservations", show=back_show, q=back_q))


# =========================================================
# CANCELAR (SOFT DELETE RECOMENDADO)
# =========================================================

@bp_admin_reservations.post("/admin/reservations/<int:purchase_id>/cancel")
@admin_required
def admin_reservation_cancel(purchase_id: int):
    back_show = (request.form.get("back_show") or "").strip()
    back_q = (request.form.get("back_q") or "").strip()
    reason = (request.form.get("reason") or "").strip()

    with db() as s:
        p = s.get(Purchase, purchase_id)
        if not p:
            abort(404)

        p.status = "cancelled"
        p.rejection_reason = reason or p.rejection_reason
        p.rejected_at = now_sp()

        s.add(p)
        s.commit()

    flash("Reserva cancelada.", "success")
    return redirect(url_for("admin_reservations.admin_reservations", show=back_show, q=back_q))


# =========================================================
# PDF PORTARIA
# =========================================================

@bp_admin_reservations.get(
    "/admin/reservations/portaria.pdf",
    endpoint="admin_reservations_portaria_pdf"
)
@admin_required
def portaria_pdf():
    show_name = (request.args.get("show") or "").strip()
    if not show_name:
        abort(400, description="Parâmetro obrigatório: show")

    q = (request.args.get("q") or "").strip().lower()

    with db() as s:
        purchases = list(
            s.scalars(
                select(Purchase)
                .where(
                    Purchase.status.in_(["reserved", "paid"]),
                    Purchase.show_name == show_name
                )
                .order_by(Purchase.created_at.asc())
            )
        )

    rows = []
    for p in purchases:
        hay = " ".join([
            p.buyer_name or "",
            p.buyer_cpf or "",
            p.buyer_email or "",
            p.buyer_phone or "",
            p.token or "",
        ]).lower()

        if q and q not in hay:
            continue

        rows.append(p)

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=1.2 * cm,
        leftMargin=1.2 * cm,
        topMargin=1.2 * cm,
        bottomMargin=1.2 * cm,
    )

    styles = getSampleStyleSheet()
    elements = []

    generated_at = now_sp().strftime("%d/%m/%Y %H:%M")

    elements.append(Paragraph("<b>Borogodó · Sons & Sabores</b>", styles["Title"]))
    elements.append(Spacer(1, 4))
    elements.append(Paragraph(f"<b>Portaria (Reservas)</b> — {show_name}", styles["Heading2"]))
    elements.append(Paragraph(f"Gerado em: {generated_at}", styles["Normal"]))
    elements.append(Spacer(1, 10))

    table_data = [["#", "Nome", "Pessoas", "Data", "Mesa"]]

    for i, p in enumerate(rows, start=1):
        created = p.created_at.strftime("%d/%m/%Y %H:%M") if p.created_at else ""
        table_data.append([
            str(i),
            p.buyer_name or "",
            str(int(p.ticket_qty or 1)),
            created,
            ""
        ])

    table = Table(table_data, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
    ]))

    elements.append(table)
    elements.append(Spacer(1, 10))
    elements.append(Paragraph(f"<b>Total de reservas:</b> {len(rows)}", styles["Normal"]))
    elements.append(Paragraph(
        f"<b>Total de pessoas:</b> {sum(int(p.ticket_qty or 1) for p in rows)}",
        styles["Heading2"]
    ))

    doc.build(elements)
    buffer.seek(0)

    filename = f"portaria-{_safe_filename(show_name)}-{now_sp().strftime('%Y%m%d%H%M')}.pdf"

    return send_file(
        buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename,
    )
