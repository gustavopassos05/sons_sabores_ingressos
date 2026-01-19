# routes/admin_delete.py
from flask import Blueprint, redirect, url_for, flash, abort
from sqlalchemy import select
from db import db
from models import Purchase, Payment, Ticket

bp_admin_delete = Blueprint("admin_delete", __name__, url_prefix="/admin")

@bp_admin_delete.post("/delete-purchase/<token>")
def delete_purchase(token):
    with db() as s:
        purchase = s.scalar(select(Purchase).where(Purchase.token == token))
        if not purchase:
            abort(404)

        # Apaga ingressos
        tickets = s.scalars(
            select(Ticket).where(Ticket.purchase_id == purchase.id)
        ).all()
        for t in tickets:
            s.delete(t)

        # Apaga pagamentos
        payments = s.scalars(
            select(Payment).where(Payment.purchase_id == purchase.id)
        ).all()
        for pay in payments:
            s.delete(pay)

        # Apaga compra
        s.delete(purchase)
        s.commit()

    flash("Compra excluída com sucesso.", "success")
    return redirect(url_for("admin_pending.admin_pending"))


@bp_admin_delete.post("/delete-ticket/<int:ticket_id>")
def delete_ticket(ticket_id):
    with db() as s:
        ticket = s.get(Ticket, ticket_id)
        if not ticket:
            abort(404)

        s.delete(ticket)
        s.commit()

    flash("Ingresso excluído com sucesso.", "success")
    return redirect(url_for("admin_tickets.admin_tickets"))
