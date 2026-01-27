# routes/mercadopago_checkout.py
import os
import uuid
import mercadopago

from flask import Blueprint, redirect, url_for, abort, current_app, request
from sqlalchemy import select, desc

from db import db
from models import Purchase, Payment

bp_mp = Blueprint("mp", __name__)

def _mp_sdk():
    token = (os.getenv("MP_ACCESS_TOKEN") or "").strip()
    if not token:
        raise RuntimeError("MP_ACCESS_TOKEN não configurado")
    return mercadopago.SDK(token)

@bp_mp.post("/pay/mp/<purchase_token>")
def mp_start(purchase_token: str):
    base_url = (os.getenv("MP_BASE_URL") or os.getenv("BASE_URL") or "").rstrip("/")
    if not base_url:
        raise RuntimeError("MP_BASE_URL/BASE_URL não configurado")

    notification_url = (os.getenv("MP_NOTIFICATION_URL") or "").strip()

    with db() as s:
        purchase = s.scalar(select(Purchase).where(Purchase.token == purchase_token))
        if not purchase:
            abort(404)

        # só faz sentido para compras com pagamento
        if (purchase.status or "").lower() != "pending_payment":
            return redirect(url_for("purchase.purchase_status", token=purchase.token))

        # cria Payment local (provider=mercadopago)
        amount_cents = int(purchase.ticket_unit_price_cents or 0) * int(purchase.ticket_qty or 1)
        pay = Payment(
            purchase_id=purchase.id,
            provider="mercadopago",
            amount_cents=amount_cents,
            currency="BRL",
            status="pending",
        )
        s.add(pay)
        s.commit()

        pay_id = pay.id

    sdk = _mp_sdk()

    # Preference (Checkout Pro)
    preference_data = {
        "items": [{
            "title": f"Sons & Sabores - {purchase.show_name}",
            "quantity": int(purchase.ticket_qty or 1),
            "unit_price": round((purchase.ticket_unit_price_cents or 0) / 100, 2),
            "currency_id": "BRL",
        }],
        "external_reference": f"purchase:{purchase_token}|payment:{pay_id}",
        "back_urls": {
            "success": f"{base_url}/mp/return/success/{purchase_token}",
            "failure": f"{base_url}/mp/return/failure/{purchase_token}",
            "pending": f"{base_url}/mp/return/pending/{purchase_token}",
        },
        "auto_return": "approved",
    }

    if notification_url:
        preference_data["notification_url"] = notification_url

    # idempotência (evita criar preferências duplicadas em clique duplo)
    request_options = mercadopago.config.RequestOptions()
    request_options.custom_headers = {"x-idempotency-key": str(uuid.uuid4())}

    result = sdk.preference().create(preference_data, request_options)
    pref = result.get("response") or {}
    init_point = pref.get("init_point") or pref.get("sandbox_init_point")
    pref_id = pref.get("id")

    if not init_point:
        current_app.logger.error("[MP] Falhou criar preferência: %s", result)
        abort(500)

    # salva external_id (preference id)
    with db() as s:
        pay = s.get(Payment, pay_id)
        if pay:
            pay.external_id = pref_id
            s.add(pay)
            s.commit()

    return redirect(init_point)

@bp_mp.get("/mp/return/<status>/<purchase_token>")
def mp_return(status: str, purchase_token: str):
    # Checkout Pro volta aqui; a confirmação final vem via webhook (mais confiável)
    return redirect(url_for("purchase.purchase_status", token=purchase_token))
