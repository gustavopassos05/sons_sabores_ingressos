# routes/mercadopago.py
import os
import uuid
from datetime import datetime

import mercadopago
from flask import Blueprint, redirect, url_for, request, abort, current_app
from sqlalchemy import select, desc

from db import db
from models import Purchase, Payment

bp_mp = Blueprint("mp", __name__)

def _sdk():
    token = (os.getenv("MP_ACCESS_TOKEN") or "").strip()
    if not token:
        raise RuntimeError("MP_ACCESS_TOKEN não configurado.")
    return mercadopago.SDK(token)

def _base_url() -> str:
    return (os.getenv("MP_BASE_URL") or os.getenv("BASE_URL") or "").strip().rstrip("/")

@bp_mp.get("/mp/start/<purchase_token>")
def mp_start(purchase_token: str):
    base_url = _base_url()
    if not base_url:
        raise RuntimeError("MP_BASE_URL/BASE_URL não configurado.")

    use_sandbox = (os.getenv("MP_USE_SANDBOX", "1").strip() != "0")

    with db() as s:
        purchase = s.scalar(select(Purchase).where(Purchase.token == purchase_token))
        if not purchase:
            abort(404)

        # Se já pago/reserva etc -> volta pro status
        if (purchase.status or "").lower() != "pending_payment":
            return redirect(url_for("purchase.purchase_status", token=purchase.token))

        # Payment mais recente ou cria
        payment = s.scalar(select(Payment).where(Payment.purchase_id == purchase.id).order_by(desc(Payment.id)))
        if not payment:
            amount_cents = int(purchase.ticket_unit_price_cents or 0) * int(purchase.ticket_qty or 1)
            payment = Payment(
                purchase_id=purchase.id,
                provider="mercadopago",
                amount_cents=amount_cents,
                currency="BRL",
                status="pending",
            )
            s.add(payment)
            s.commit()

        pay_id = payment.id

    sdk = _sdk()

    preference_data = {
        "items": [{
            "title": f"Sons & Sabores - {purchase.show_name}",
            "quantity": int(purchase.ticket_qty or 1),
            "unit_price": round((purchase.ticket_unit_price_cents or 0) / 100, 2),
            "currency_id": "BRL",
        }],
        # amarra no seu sistema (vamos ler isso no webhook)
        "external_reference": f"purchase:{purchase_token}|payment:{pay_id}",
        "back_urls": {
            "success": f"{base_url}/mp/return/success/{purchase_token}",
            "pending": f"{base_url}/mp/return/pending/{purchase_token}",
            "failure": f"{base_url}/mp/return/failure/{purchase_token}",
        },
        "auto_return": "approved",
    }

    # idempotência (evita duplicar preferência por duplo clique)
    request_options = mercadopago.config.RequestOptions()
    request_options.custom_headers = {"x-idempotency-key": str(uuid.uuid4())}

    result = sdk.preference().create(preference_data, request_options)
    pref = result.get("response") or {}

    init_point = pref.get("sandbox_init_point") if use_sandbox else pref.get("init_point")
    pref_id = pref.get("id")

    if not init_point:
        current_app.logger.error("[MP] preference error: %s", result)
        abort(500)

    # salva id da preferência no Payment
    with db() as s:
        pay_db = s.get(Payment, pay_id)
        if pay_db:
            pay_db.external_id = pref_id
            s.add(pay_db)
            s.commit()

    return redirect(init_point)

@bp_mp.get("/mp/return/<status>/<purchase_token>")
def mp_return(status: str, purchase_token: str):
    # confirmação real via webhook; aqui só volta pro status
    return redirect(url_for("purchase.purchase_status", token=purchase_token))

@bp_mp.post("/webhooks/mercadopago")
def mp_webhook():
    # MP manda normalmente: ?type=payment&data.id=...
    mp_type = (request.args.get("type") or "").lower().strip()
    data_id = request.args.get("data.id") or request.args.get("id")

    payload = request.get_json(silent=True) or {}
    if not data_id:
        data = payload.get("data") or {}
        data_id = data.get("id")

    # ignorar outros tipos
    if mp_type and mp_type != "payment":
        return {"ok": True}

    if not data_id:
        current_app.logger.info("[MP WEBHOOK] sem data.id payload=%s", payload)
        return {"ok": True}

    sdk = _sdk()
    try:
        res = sdk.payment().get(str(data_id))
        mp_payment = (res.get("response") or {})
    except Exception as e:
        current_app.logger.warning("[MP WEBHOOK] erro consultando payment %s: %s", data_id, e)
        return {"ok": True}

    mp_status = (mp_payment.get("status") or "").lower()
    ext_ref = (mp_payment.get("external_reference") or "").strip()

    # purchase:<token>|payment:<id>
    purchase_token = ""
    local_payment_id = None
    try:
        for part in ext_ref.split("|"):
            if part.startswith("purchase:"):
                purchase_token = part.split("purchase:", 1)[1].strip()
            if part.startswith("payment:"):
                local_payment_id = int(part.split("payment:", 1)[1].strip())
    except Exception:
        pass

    if not purchase_token:
        current_app.logger.warning("[MP WEBHOOK] external_reference inválido: %s", ext_ref)
        return {"ok": True}

    if mp_status == "approved":
        finalize_fn = current_app.extensions.get("finalize_purchase")

        with db() as s:
            purchase = s.scalar(select(Purchase).where(Purchase.token == purchase_token))
            if not purchase:
                return {"ok": True}

            payment = s.get(Payment, local_payment_id) if local_payment_id else None
            if not payment:
                payment = s.scalar(select(Payment).where(Payment.purchase_id == purchase.id).order_by(desc(Payment.id)))
            if not payment:
                return {"ok": True}

            # idempotência
            if (purchase.status or "").lower() == "paid" and (payment.status or "").lower() == "paid":
                return {"ok": True}

            payment.status = "paid"
            payment.paid_at = datetime.utcnow()
            payment.provider = "mercadopago"
            payment.external_id = str(mp_payment.get("id") or payment.external_id)

            purchase.status = "paid"
            s.add(payment)
            s.add(purchase)
            s.commit()
            purchase_id = purchase.id

        if callable(finalize_fn):
            finalize_fn(purchase_id)

    return {"ok": True}
