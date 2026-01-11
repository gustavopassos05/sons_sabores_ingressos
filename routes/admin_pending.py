# routes/admin_pending.py
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, current_app
from sqlalchemy import select, desc

from db import db
from models import Purchase, Payment
from routes.admin_auth import admin_required
from services.email_service import send_receipt_email


bp_admin_pending = Blueprint("admin_pending", __name__)

@bp_admin_pending.get("/admin/pending")
@admin_required
def admin_pending():
    q = (request.args.get("q") or "").strip().lower()

    with db() as s:
        purchases = list(s.scalars(select(Purchase).order_by(desc(Purchase.id)).limit(300)))

        rows = []
        for p in purchases:
            pay = s.scalar(
                select(Payment).where(Payment.purchase_id == p.id).order_by(desc(Payment.id))
            )
            if not pay:
                continue
            if (pay.status or "").lower() == "paid":
                continue

            hay = " ".join([(p.buyer_name or ""), (p.buyer_cpf or ""), (p.show_name or ""), (p.token or "")]).lower()
            if q and q not in hay:
                continue

            rows.append({"purchase": p, "payment": pay})

    return render_template("admin_pending.html", rows=rows, q=q)

@bp_admin_pending.post("/admin/mark-paid/<purchase_token>")
@admin_required
def admin_mark_paid(purchase_token: str):
    finalize_fn = current_app.extensions.get("finalize_purchase")

    with db() as s:
        purchase = s.scalar(select(Purchase).where(Purchase.token == purchase_token))
        if not purchase:
            abort(404)

        payment = s.scalar(
            select(Payment).where(Payment.purchase_id == purchase.id).order_by(desc(Payment.id))
        )
        if not payment:
            abort(404)

        payment.status = "paid"
        payment.paid_at = datetime.utcnow()
        purchase.status = "paid"

        s.add(payment)
        s.add(purchase)
        # seu db() já comita automático

    if callable(finalize_fn):
        finalize_fn(purchase.id)

    return {"ok": True}
