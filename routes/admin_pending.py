# routes/admin_pending.py
import os
from datetime import datetime
from flask import Blueprint, request, abort, render_template, jsonify, current_app
from sqlalchemy import select, desc

from db import db
from models import Purchase, Payment

bp_admin_pending = Blueprint("admin_pending", __name__)

def _check_admin():
    key = (os.getenv("ADMIN_KEY") or "").strip()
    if not key:
        raise RuntimeError("ADMIN_KEY não configurado no Render.")
    got = (request.headers.get("X-ADMIN-KEY") or request.args.get("key") or "").strip()
    if got != key:
        abort(401)

@bp_admin_pending.get("/admin/pending")
def admin_pending():
    _check_admin()
    q = (request.args.get("q") or "").strip().lower()
    key = (request.args.get("key") or "").strip()

    with db() as s:
        purchases = list(
            s.scalars(
                select(Purchase)
                .order_by(desc(Purchase.id))
                .limit(300)
            )
        )

        rows = []
        for p in purchases:
            pay = s.scalar(
                select(Payment)
                .where(Payment.purchase_id == p.id)
                .order_by(desc(Payment.id))
            )

            # só mostrar pendentes (manual e pagbank)
            if not pay or (pay.status or "").lower() == "paid":
                continue

            hay = " ".join([
                (p.buyer_name or ""),
                (p.buyer_cpf or ""),
                (p.show_name or ""),
                (p.token or "")
            ]).lower()

            if q and q not in hay:
                continue

            rows.append({"purchase": p, "payment": pay})

    return render_template(
        "admin_pending.html",
        rows=rows,
        q=q,
        admin_key=key,
        app_name=os.getenv("APP_NAME", "Sons & Sabores"),
    )

@bp_admin_pending.post("/admin/mark-paid/<purchase_token>")
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

        payment.status = "paid"
        payment.paid_at = datetime.utcnow()
        purchase.status = "paid"

        s.add(payment)
        s.add(purchase)
        s.commit()

    if callable(finalize_fn):
        finalize_fn(purchase.id)

    return jsonify({"ok": True})
