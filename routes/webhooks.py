# routes/webhooks.py
from datetime import datetime

from flask import Blueprint, request, abort, current_app
from sqlalchemy import select

from db import db
from models import Payment, Purchase

bp_webhooks = Blueprint("webhooks", __name__)


def _mark_paid_and_finalize(purchase: Purchase, payment: Payment):
    # marca pago
    payment.status = "paid"
    payment.paid_at = datetime.utcnow()
    purchase.status = "paid"

    # se existir finalize_purchase no app, gera tickets/arquivos
    finalize = current_app.extensions.get("finalize_purchase")
    if callable(finalize):
        # finalize_purchase_factory normalmente retorna uma função finalize(purchase_id)
        try:
            finalize(purchase.id)
        except TypeError:
            # caso seu finalize espere outro formato
            finalize(purchase)


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

        purchase = s.get(Purchase, payment.purchase_id) if payment.purchase_id else None
        if not purchase:
            abort(404)

        _mark_paid_and_finalize(purchase, payment)

    return {"ok": True}


@bp_webhooks.post("/webhooks/pagseguro")
def pagseguro_webhook():
    """
    PagSeguro Classic normalmente envia:
      notificationType=transaction
      notificationCode=...
    """
    notification_code = (request.form.get("notificationCode") or "").strip()
    notification_type = (request.form.get("notificationType") or "").strip().lower()

    if not notification_code:
        abort(400)

    # só tratamos transaction
    if notification_type and notification_type != "transaction":
        return {"ok": True}

    from app_services.payments.pagseguro_notify import fetch_transaction_by_notification

    tx = fetch_transaction_by_notification(notification_code)
    reference = (tx.get("reference") or "").strip()  # ex: purchase-123
    status = int(tx.get("status") or 0)
    tx_code = (tx.get("code") or "").strip()         # transaction code

    # considera pago quando status 3 (paga) ou 4 (disponível)
    is_paid = status in (3, 4)
    if not is_paid:
        return {"ok": True}

    # extrai purchase_id do reference "purchase-<id>"
    purchase_id = None
    if reference.startswith("purchase-"):
        try:
            purchase_id = int(reference.split("-", 1)[1])
        except Exception:
            purchase_id = None

    if not purchase_id:
        # fallback: tenta achar por external_id (se você salvou o checkout_code como external_id,
        # o tx_code é diferente — então reference é o melhor caminho)
        abort(400)

    with db() as s:
        purchase = s.get(Purchase, purchase_id)
        if not purchase:
            abort(404)

        # pega o payment "pending" do pagseguro desta compra
        payment = s.scalar(
            select(Payment)
            .where(Payment.purchase_id == purchase.id, Payment.provider == "pagseguro")
            .order_by(Payment.id.desc())
        )
        if not payment:
            # se não existir, cria um payment básico pra registrar a confirmação
            payment = Payment(
                purchase_id=purchase.id,
                provider="pagseguro",
                amount_cents=0,
                currency="BRL",
                status="pending",
                external_id=None,
            )
            s.add(payment)
            s.flush()

        # opcional: guarda o transaction code como external_id (útil pra auditoria)
        if tx_code:
            payment.external_id = tx_code

        _mark_paid_and_finalize(purchase, payment)

    return {"ok": True}
