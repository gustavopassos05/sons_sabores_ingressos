# routes/admin_purchases.py
from flask import Blueprint, render_template, request, abort, current_app
from sqlalchemy import select, desc, func
from datetime import datetime

from db import db
from models import Purchase, Payment, Ticket
from routes.admin_auth import admin_required

bp_admin_purchases = Blueprint("admin_purchases", __name__)

@bp_admin_purchases.get("/admin/purchases")
@admin_required
def admin_purchases_table():
    q = (request.args.get("q") or "").strip().lower()

    with db() as s:
        purchases = list(s.scalars(select(Purchase).order_by(desc(Purchase.id)).limit(400)))

        rows = []
        for p in purchases:
            pay_paid = s.scalar(
                select(Payment)
                .where(Payment.purchase_id == p.id, Payment.status == "paid")
                .order_by(desc(Payment.id))
            )
            pay = pay_paid or s.scalar(
                select(Payment)
                .where(Payment.purchase_id == p.id)
                .order_by(desc(Payment.id))
            )

            ticket_count = s.scalar(
                select(func.count()).select_from(Ticket).where(Ticket.purchase_id == p.id)
            ) or 0

            hay = " ".join([
                (p.buyer_name or ""),
                (p.buyer_cpf or ""),
                (p.show_name or ""),
                (p.token or ""),
                (pay.provider if pay else ""),
                (pay.status if pay else ""),
            ]).lower()

            if q and q not in hay:
                continue

            rows.append({
                "purchase": p,
                "payment": pay,
                "ticket_count": ticket_count,
            })

    return render_template("admin_purchases_table.html", rows=rows, q=q)
