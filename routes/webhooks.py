# routes/webhooks.py
from datetime import datetime
from flask import Blueprint, request, abort, current_app
from sqlalchemy import select

from db import db
from models import Payment, Purchase

bp_webhooks = Blueprint("webhooks", __name__)

def _mark_paid_and_finalize(s, purchase: Purchase, payment: Payment):
    # idempotência
    if (payment.status or "").lower() == "paid" and (purchase.status or "").lower() == "paid":
        return

    payment.status = "paid"
    payment.paid_at = datetime.utcnow()
    purchase.status = "paid"

    s.add(payment)
    s.add(purchase)
    s.commit()

    finalize = current_app.extensions.get("finalize_purchase")
    if callable(finalize):
        finalize(purchase.id)

@bp_webhooks.post("/webhooks/pagbank")
def pagbank_webhook():
    print("[WEBHOOK] pagbank HIT", request.headers.get("User-Agent"), request.content_type)

    payload = request.get_json(silent=True) or {}   # ✅ NÃO dá 415
    reference_id = (payload.get("reference_id") or "").strip()
    status = (payload.get("status") or "").strip().upper()

    if reference_id.startswith("purchase-") and status:
        if status != "PAID":
            return {"ok": True}

        try:
            purchase_id = int(reference_id.split("-", 1)[1])
        except Exception:
            abort(400)

        with db() as s:
            purchase = s.get(Purchase, purchase_id)
            if not purchase:
                abort(404)

            payment = s.scalar(
                select(Payment)
                .where(Payment.purchase_id == purchase.id)
                .order_by(Payment.id.desc())
            )
            if not payment:
                abort(404)

            _mark_paid_and_finalize(s, purchase, payment)

        return {"ok": True}

    # compat antigo
    event = payload.get("event")
    charge_id = payload.get("charge_id")
    if event == "PAYMENT_CONFIRMED" and charge_id:
        with db() as s:
            payment = s.scalar(select(Payment).where(Payment.external_id == charge_id))
            if not payment:
                abort(404)

            purchase = s.get(Purchase, payment.purchase_id) if payment.purchase_id else None
            if not purchase:
                abort(404)

            _mark_paid_and_finalize(s, purchase, payment)

        return {"ok": True}

    return {"ok": True}

@bp_webhooks.post("/webhooks/pagbank-checkout")
def pagbank_checkout_webhook():
    print("[WEBHOOK] pagbank-checkout HIT", request.headers.get("User-Agent"), request.content_type)

    payload = request.get_json(silent=True) or {}   # ✅ NÃO dá 415
    reference_id = (payload.get("reference_id") or "").strip()
    status = (payload.get("status") or "").strip().upper()

    if not reference_id.startswith("purchase-") or not status:
        return {"ok": True}

    if status != "PAID":
        return {"ok": True}

    try:
        purchase_id = int(reference_id.split("-", 1)[1])
    except Exception:
        abort(400)

    with db() as s:
        purchase = s.get(Purchase, purchase_id)
        if not purchase:
            abort(404)

        payment = s.scalar(
            select(Payment)
            .where(Payment.purchase_id == purchase.id, Payment.provider == "pagbank_checkout")
            .order_by(Payment.id.desc())
        )
        if not payment:
            abort(404)

        _mark_paid_and_finalize(s, purchase, payment)

    return {"ok": True}
