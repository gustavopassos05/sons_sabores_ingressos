# routes/webhooks.py
from datetime import datetime
from flask import Blueprint, request, abort, current_app
from sqlalchemy import select

from db import db
from models import Payment, Purchase

bp_webhooks = Blueprint("webhooks", __name__)

def _mark_paid_and_finalize(purchase: Purchase, payment: Payment):
    payment.status = "paid"
    payment.paid_at = datetime.utcnow()
    purchase.status = "paid"

    finalize = current_app.extensions.get("finalize_purchase")
    if callable(finalize):
        try:
            finalize(purchase.id)
        except TypeError:
            finalize(purchase)

@bp_webhooks.post("/webhooks/pagbank")
def pagbank_webhook():
    payload = request.json or {}

    # ✅ caminho 1: payload estilo "charge"
    # Exemplo típico tem: id, reference_id, status, ...
    reference_id = (payload.get("reference_id") or "").strip()
    status = (payload.get("status") or "").strip().upper()

    if reference_id.startswith("purchase-") and status:
        # considera pago quando PAID
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
                .where(Payment.purchase_id == purchase.id, Payment.provider == "pagbank")
                .order_by(Payment.id.desc())
            )
            if not payment:
                abort(404)

            _mark_paid_and_finalize(purchase, payment)

        return {"ok": True}

    # ✅ caminho 2: compat com seu formato antigo
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

            _mark_paid_and_finalize(purchase, payment)

        return {"ok": True}

    return {"ok": True}

@bp_webhooks.post("/webhooks/pagbank-checkout")
def pagbank_checkout_webhook():
    payload = request.json or {}

    # payload exemplo tem: id, reference_id, status :contentReference[oaicite:3]{index=3}
    reference_id = (payload.get("reference_id") or "").strip()
    status = (payload.get("status") or "").strip().upper()

    # no checkout, status transacional pode ser PAID, WAITING, DECLINED... :contentReference[oaicite:4]{index=4}
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

        _mark_paid_and_finalize(purchase, payment)

    return {"ok": True}
