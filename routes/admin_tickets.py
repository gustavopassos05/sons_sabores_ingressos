# routes/admin_tickets.py
from flask import Blueprint, render_template, request
from sqlalchemy import select, desc

from db import db
from models import Ticket, Purchase, Payment
from routes.admin_auth import admin_required

bp_admin_tickets = Blueprint("admin_tickets", __name__)

@bp_admin_tickets.get("/admin/tickets")
@admin_required
def admin_tickets_table():
    q = (request.args.get("q") or "").strip().lower()

    with db() as s:
        tickets = list(s.scalars(select(Ticket).order_by(desc(Ticket.id)).limit(600)))

        purchase_ids = sorted({t.purchase_id for t in tickets if t.purchase_id})
        purchases_map = {}
        payments_map = {}

        if purchase_ids:
            purchases = list(s.scalars(select(Purchase).where(Purchase.id.in_(purchase_ids))))
            purchases_map = {p.id: p for p in purchases}

            payments = list(s.scalars(select(Payment).where(Payment.purchase_id.in_(purchase_ids)).order_by(desc(Payment.id))))
            for pay in payments:
                if pay.purchase_id and pay.purchase_id not in payments_map:
                    payments_map[pay.purchase_id] = pay

        rows = []
        for t in tickets:
            p = purchases_map.get(t.purchase_id) if t.purchase_id else None
            pay = payments_map.get(t.purchase_id) if t.purchase_id else None

            buyer = (p.buyer_name if p else t.buyer_name) or ""
            show = (t.show_name or "").strip()
            person = (t.person_name or "").strip()
            cpf = (p.buyer_cpf if p else "") or ""
            token = (p.token if p else "") or ""

            hay = " ".join([buyer, show, person, cpf, token]).lower()
            if q and q not in hay:
                continue

            rows.append({"ticket": t, "purchase": p, "payment": pay})

    return render_template("admin_tickets_table.html", rows=rows, q=q)
