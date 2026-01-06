# routes/webhooks.py
from datetime import datetime
from flask import Blueprint, request, abort, current_app
from sqlalchemy import select

from db import db
from models import Payment, Purchase

bp_webhooks = Blueprint("webhooks", __name__)


def _get_payload() -> dict:
    """
    PagBank às vezes manda JSON, às vezes manda form-urlencoded.
    Aqui a gente aceita os dois sem dar 415.
    """
    payload = request.get_json(silent=True)
    if isinstance(payload, dict) and payload:
        return payload

    # fallback: x-www-form-urlencoded
    form = request.form.to_dict(flat=True)
    if isinstance(form, dict) and form:
        return form

    return {}


def _maybe_finalize(purchase_id: int) -> None:
    finalize = current_app.extensions.get("finalize_purchase")
    if callable(finalize):
        try:
            finalize(purchase_id)
        except Exception as e:
            # ⚠️ não derruba o webhook (PagBank pode reenviar)
            current_app.logger.exception("[FINALIZE] erro ao finalizar purchase=%s: %s", purchase_id, e)


def _mark_paid_and_finalize(s, purchase: Purchase, payment: Payment):
    """
    Marca como pago (se ainda não estiver) e SEMPRE tenta finalizar
    se estiver pago e ainda não tiver URLs de download.
    """
    purchase_paid = (purchase.status or "").lower() == "paid"
    payment_paid = (payment.status or "").lower() == "paid"

    # Se ainda não marcou pago, marca e commita
    if not purchase_paid or not payment_paid:
        payment.status = "paid"
        payment.paid_at = payment.paid_at or datetime.utcnow()
        purchase.status = "paid"

        s.add(payment)
        s.add(purchase)
        s.commit()

    # Mesmo se já estava pago: se não tem links ainda, tenta finalizar.
    if not (getattr(payment, "tickets_pdf_url", None) or getattr(payment, "tickets_zip_url", None)):
        _maybe_finalize(purchase.id)


def _digits(s: str) -> str:
    return "".join(c for c in (s or "") if c.isdigit())


def _get_reference_id(payload: dict) -> str:
    # PagBank pode variar o nome do campo
    return str(
        payload.get("reference_id")
        or payload.get("referenceId")
        or payload.get("reference")
        or ""
    ).strip()


def _get_status(payload: dict) -> str:
    # Pode vir paid/PAID etc.
    return str(payload.get("status") or "").strip().upper()


def _get_external_id(payload: dict) -> str:
    # Alguns payloads trazem id do pedido/charge/checkout
    ext = (
        payload.get("id")
        or payload.get("order_id")
        or payload.get("orderId")
        or payload.get("charge_id")
        or payload.get("chargeId")
        or ""
    )
    return str(ext).strip()


@bp_webhooks.post("/webhooks/pagbank")
def pagbank_webhook():
    print("[WEBHOOK] pagbank HIT", request.headers.get("User-Agent"), request.content_type)

    payload = _get_payload()

    reference_id = _get_reference_id(payload)
    status = _get_status(payload)
    external_id = _get_external_id(payload)

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

            # ✅ PIX: procure o payment do provider pagbank (e de preferência pelo external_id)
            q = (
                select(Payment)
                .where(Payment.purchase_id == purchase.id, Payment.provider == "pagbank")
                .order_by(Payment.id.desc())
            )
            if external_id:
                q = q.where(Payment.external_id == external_id)

            payment = s.scalar(q)

            if not payment:
                # fallback: pega o último pix mesmo sem external_id bater
                payment = s.scalar(
                    select(Payment)
                    .where(Payment.purchase_id == purchase.id, Payment.provider == "pagbank")
                    .order_by(Payment.id.desc())
                )

            if not payment:
                abort(404)

            _mark_paid_and_finalize(s, purchase, payment)

        return {"ok": True}

    # caminho 2: compat antigo
    event = payload.get("event")
    charge_id = payload.get("charge_id") or payload.get("chargeId")
    if event == "PAYMENT_CONFIRMED" and charge_id:
        with db() as s:
            payment = s.scalar(
                select(Payment)
                .where(Payment.external_id == str(charge_id).strip())
                .order_by(Payment.id.desc())
            )
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

    payload = _get_payload()

    reference_id = _get_reference_id(payload)
    status = _get_status(payload)

    checkout_id = _get_external_id(payload)

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

        # ✅ Checkout: procure provider pagbank_checkout (preferindo external_id do payload)
        q = (
            select(Payment)
            .where(Payment.purchase_id == purchase.id, Payment.provider == "pagbank_checkout")
            .order_by(Payment.id.desc())
        )
        if checkout_id:
            q = q.where(Payment.external_id == checkout_id)

        payment = s.scalar(q)

        if not payment:
            # fallback: último checkout
            payment = s.scalar(
                select(Payment)
                .where(Payment.purchase_id == purchase.id, Payment.provider == "pagbank_checkout")
                .order_by(Payment.id.desc())
            )

        if not payment:
            abort(404)

        _mark_paid_and_finalize(s, purchase, payment)

    return {"ok": True}
