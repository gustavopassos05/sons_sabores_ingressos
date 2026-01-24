# routes/admin_delete.py
from urllib.parse import urlparse

from flask import Blueprint, redirect, url_for, flash, abort, request
from sqlalchemy import select

from db import db
from models import Purchase, Payment, Ticket
from routes.admin_auth import admin_required  # ✅ importante proteger


bp_admin_delete = Blueprint("admin_delete", __name__, url_prefix="/admin")


def _safe_next(next_url: str) -> str:
    """Permite apenas redirects internos (sem scheme/netloc)."""
    if not next_url:
        return ""
    u = urlparse(next_url)
    if u.scheme or u.netloc:
        return ""
    return next_url


@bp_admin_delete.post("/delete-purchase/<token>")
@admin_required
def delete_purchase(token):
    with db() as s:
        purchase = s.scalar(select(Purchase).where(Purchase.token == token))
        if not purchase:
            abort(404)

        # Apaga ingressos
        tickets = s.scalars(select(Ticket).where(Ticket.purchase_id == purchase.id)).all()
        for t in tickets:
            s.delete(t)

        # Apaga pagamentos
        payments = s.scalars(select(Payment).where(Payment.purchase_id == purchase.id)).all()
        for pay in payments:
            s.delete(pay)

        # Apaga compra
        s.delete(purchase)
        s.commit()

    flash("Compra excluída com sucesso.", "success")

    next_url = _safe_next(request.args.get("next", ""))

    # ✅ fallback CORRETO (se next não vier)
    return redirect(next_url or url_for("admin_pending.admin_pending"))


@bp_admin_delete.post("/delete-ticket/<int:ticket_id>")
@admin_required
def delete_ticket(ticket_id):
    with db() as s:
        ticket = s.get(Ticket, ticket_id)
        if not ticket:
            abort(404)

        s.delete(ticket)
        s.commit()

    flash("Ingresso excluído com sucesso.", "success")

    next_url = _safe_next(request.args.get("next", ""))

    # ✅ fallback CORRETO (ajuste para o seu endpoint real)
    return redirect(next_url or url_for("admin_tickets.admin_tickets_table"))
