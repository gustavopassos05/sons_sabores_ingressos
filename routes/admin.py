# routes/admin.py
import os
from datetime import datetime

from flask import Blueprint, abort, request, current_app
from sqlalchemy import select

from db import db
from models import Purchase, Payment

bp_admin = Blueprint("admin", __name__)

def _check_admin_key():
    key = (os.getenv("ADMIN_KEY") or "").strip()
    if not key:
        raise RuntimeError("Configure ADMIN_KEY nas env vars do Render.")
    got = (request.headers.get("X-ADMIN-KEY") or "").strip()
    if got != key:
        abort(401)


@bp_admin.get("/admin/ping")
def admin_ping():
    # útil pra testar rapidamente se o blueprint está ativo
    return {"ok": True, "env": os.getenv("PAGBANK_ENV", "sandbox")}


@bp_admin.post("/admin/simulate-paid/<token>")
def simulate_paid(token: str):
    _check_admin_key()

    with db() as s:
        purchase = s.scalar(select(Purchase).where(Purchase.token == token))
        if not purchase:
            abort(404)

        payment = s.scalar(
            select(Payment)
            .where(Payment.purchase_id == purchase.id)
            .order_by(Payment.id.desc())
        )
        if not payment:
            abort(404)

        purchase.status = "paid"
        payment.status = "paid"
        payment.paid_at = datetime.utcnow()

        s.add(purchase)
        s.add(payment)
        # seu contextmanager já dá commit automaticamente

    finalize = current_app.extensions.get("finalize_purchase")
    if callable(finalize):
        finalize(purchase.id)

    return {"ok": True, "token": token, "purchase_id": purchase.id, "payment_id": payment.id}
