from flask import Blueprint, request, abort
from db import db
from models import Payment, Purchase
from sqlalchemy import select
from datetime import datetime

bp_webhooks = Blueprint("webhooks", __name__)


@bp_webhooks.post("/webhooks/pagbank")
def pagbank_webhook():
    payload = request.json or {}
    event = payload.get("event")

    if event != "PAYMENT_CONFIRMED":
        return {"ok": True}

    charge_id = payload.get("charge_id")
    if not charge_id:
        abort(400)

    with db() as s:
        payment = s.scalar(select(Payment).where(Payment.external_id == charge_id))
        if not payment:
            abort(404)

        payment.status = "paid"
        payment.paid_at = datetime.utcnow()

        purchase = s.get(Purchase, payment.purchase_id)
        if purchase:
            purchase.status = "paid"

    return {"ok": True}
