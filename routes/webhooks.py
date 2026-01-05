# routes/webhooks.py
from datetime import datetime
from flask import Blueprint, request, abort, current_app
from sqlalchemy import select

from db import db
from models import Payment, Purchase

bp_webhooks = Blueprint("webhooks", __name__)


def _payload_any() -> dict:
    """
    Evita 415 e aceita:
    - JSON (application/json)
    - form-urlencoded (request.form)
    - body vazio
    """
    data = request.get_json(silent=True)
    if isinstance(data, dict) and data:
        return data

    # alguns webhooks chegam como form (ou sem content-type)
    if request.form:
        return dict(request.form)

    # fallback: tenta querystring
    if request.args:
        return dict(request.args)

    return {}


def _mark_paid_and_finalize(s, purchase: Purchase, payment: Payment) -> None:
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

    payload = _payload_any()

    # PIX Orders API (seu create_pix_order usa reference_id = purchase-<id>)
    reference_id = (payload.get("reference_id") or payload.get("referenceId") or "").strip()
    status = (payload.get("status") or "").strip().upper()

    # caminho 1: reference_id = purchase-<id>
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
                .where(Payment.purchase_id == purchase.id, Payment.provider == "pagbank")
                .order_by(Payment.id.desc())
            )
            if not payment:
                abort(404)

            _mark_paid_and_finalize(s, purchase, payment)

        return {"ok": True}

    # caminho 2: compat antigo (se algum dia você usou isso)
    event = (payload.get("event") or "").strip()
    charge_id = (payload.get("charge_id") or payload.get("chargeId") or "").strip()
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

    # não entendeu o payload → não falha (PagBank reenvia e pode derrubar)
    return {"ok": True}


@bp_webhooks.post("/webhooks/pagbank-checkout")
def pagbank_checkout_webhook():
    print("[WEBHOOK] pagbank-checkout HIT", request.headers.get("User-Agent"), request.content_type)

    payload = _payload_any()

    # ✅ Checkout Redirect (PagSeguro clássico) muitas vezes manda NOTIFICATIONCODE
    # e NÃO manda JSON com status. Ex: notificationCode=XXXX
    notification_code = (payload.get("notificationCode") or payload.get("notification_code") or "").strip()
    if notification_code:
        # você pode (opcional) implementar a consulta de status via API aqui.
        # mas no sandbox, para seu fluxo atual, a gente só registra que chegou.
        print("[WEBHOOK] checkout notificationCode=", notification_code)
        return {"ok": True}

    # ✅ Se vier no mesmo formato do PIX (reference_id/status), processa também:
    reference_id = (payload.get("reference_id") or payload.get("referenceId") or "").strip()
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
