# routes/tickets.py
from flask import Blueprint, render_template, abort
from sqlalchemy import select

from db import db
from models import Purchase, Ticket, Payment

bp_tickets = Blueprint("tickets", __name__)

@bp_tickets.get("/admin")
def admin_tickets():
    return render_template("admin_tickets.html")

@bp_tickets.get("/purchase/<token>")
def purchase_public(token: str):
    with db() as s:
        purchase = s.scalar(select(Purchase).where(Purchase.token == token))
        if not purchase:
            abort(404)

        tickets = list(
            s.scalars(
                select(Ticket)
                .where(Ticket.purchase_id == purchase.id)
                .order_by(Ticket.id.asc())
            )
        )

        payment = s.scalar(
            select(Payment)
            .where(Payment.purchase_id == purchase.id)
            .order_by(Payment.id.desc())
        )

    return render_template("purchase_public.html", purchase=purchase, tickets=tickets, payment=payment)
