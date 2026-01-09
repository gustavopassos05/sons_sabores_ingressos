# routes/admin_tickets.py
import os
from datetime import datetime
from flask import Blueprint, request, abort, render_template, jsonify, current_app
from sqlalchemy import select, desc

from db import db
from models import Ticket, Purchase, Payment

bp_admin_tickets = Blueprint("admin_tickets", __name__)


def _check_admin():
    key = (os.getenv("ADMIN_KEY") or "").strip()
    if not key:
        raise RuntimeError("ADMIN_KEY não configurado no Render.")
    got = (request.headers.get("X-ADMIN-KEY") or request.args.get("key") or "").strip()
    if got != key:
        abort(401)


@bp_admin_tickets.get("/admin/tickets")
def admin_tickets_table():
    _check_admin()
    q = (request.args.get("q") or "").strip().lower()
    key = (request.args.get("key") or "").strip()

    with db() as s:
        # tickets recentes
        tickets = list(s.scalars(select(Ticket).order_by(desc(Ticket.id)).limit(600)))

        purchase_ids = sorted({t.purchase_id for t in tickets if t.purchase_id})
        purchases_map = {}
        payments_map = {}

        if purchase_ids:
            purchases = list(s.scalars(select(Purchase).where(Purchase.id.in_(purchase_ids))))
            purchases_map = {p.id: p for p in purchases}

            # último payment por purchase
            payments = list(
                s.scalars(
                    select(Payment)
                    .where(Payment.purchase_id.in_(purchase_ids))
                    .order_by(desc(Payment.id))
                )
            )
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

    return render_template(
        "admin_tickets_table.html",
        rows=rows,
        q=q,
        admin_key=key,
        app_name=os.getenv("APP_NAME", "Sons & Sabores"),
    )


@bp_admin_tickets.post("/admin/mark-paid/<purchase_token>")
def admin_mark_paid(purchase_token: str):
    _check_admin()

    finalize_fn = current_app.extensions.get("finalize_purchase")

    with db() as s:
        purchase = s.scalar(select(Purchase).where(Purchase.token == purchase_token))
        if not purchase:
            abort(404)

        payment = s.scalar(
            select(Payment)
            .where(Payment.purchase_id == purchase.id)
            .order_by(desc(Payment.id))
        )
        if not payment:
            abort(404)

        # marca paid
        if (payment.status or "").lower() != "paid":
            payment.status = "paid"
            payment.paid_at = datetime.utcnow()
            purchase.status = "paid"
            s.add(payment)
            s.add(purchase)
            s.commit()

    # roda finalize fora da sessão
    if callable(finalize_fn):
        finalize_fn(purchase.id)

    return jsonify({"ok": True})
