# routes/admin_delete.py
from flask import Blueprint, redirect, url_for, flash, abort, request
from sqlalchemy import select

from db import db
from models import Purchase, Payment, Ticket
from routes.admin_auth import admin_required

bp_admin_delete = Blueprint("admin_delete", __name__)

@bp_admin_delete.post("/admin/delete/purchase/<token>")
@admin_required
def delete_purchase(token: str):
    # opcional: confirmação extra por form
    # if request.form.get("confirm") != "yes": abort(400)

    with db() as s:
        purchase = s.scalar(select(Purchase).where(Purchase.token == token))
        if not purchase:
            abort(404)

        # apaga tickets
        s.execute(
            Ticket.__table__.delete().where(Ticket.purchase_id == purchase.id)
        )
        # apaga payments
        s.execute(
            Payment.__table__.delete().where(Payment.purchase_id == purchase.id)
        )
        # apaga purchase
        s.execute(
            Purchase.__table__.delete().where(Purchase.id == purchase.id)
        )

    flash("Compra excluída ✅", "success")

    # volta pra tela que chamou (fallback)
    ref = request.referrer or ""
    if "/admin/pending" in ref:
        return redirect(url_for("admin_pending.admin_pending"))
    if "/admin/tickets" in ref:
        return redirect(url_for("admin_tickets.admin_tickets_table"))
    if "/admin/purchases" in ref:
        return redirect(url_for("admin_purchases.admin_purchases_table"))
    return redirect(url_for("admin_panel.home"))

@bp_admin_delete.post("/admin/delete/ticket/<int:ticket_id>")
@admin_required
def delete_ticket(ticket_id: int):
    with db() as s:
        t = s.get(Ticket, ticket_id)
        if not t:
            abort(404)
        s.execute(Ticket.__table__.delete().where(Ticket.id == ticket_id))

    flash("Ingresso excluído ✅", "success")

    ref = request.referrer or ""
    if "/admin/tickets" in ref:
        return redirect(url_for("admin_tickets.admin_tickets_table"))
    return redirect(url_for("admin_panel.home"))
