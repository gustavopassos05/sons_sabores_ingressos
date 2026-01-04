# app_services/payments/pagseguro_notify.py
import os
import xml.etree.ElementTree as ET
from datetime import datetime

import requests
from flask import Blueprint, request, abort, current_app
from sqlalchemy import select

from db import db
from models import Payment, Purchase

bp_pagseguro_notify = Blueprint("pagseguro_notify", __name__)


def _env() -> str:
    return (os.getenv("PAGSEGURO_ENV", "sandbox") or "sandbox").lower().strip()


def _credentials() -> tuple[str, str]:
    email = (os.getenv("PAGSEGURO_EMAIL") or "").strip()
    token = (os.getenv("PAGSEGURO_TOKEN") or "").strip()
    if not email or not token:
        raise RuntimeError("PAGSEGURO_EMAIL e PAGSEGURO_TOKEN precisam estar configurados nas env vars do Render.")
    return email, token


def transactions_notification_url(notification_code: str) -> str:
    # PagSeguro Classic - consultar transação via notificationCode (XML)
    # sandbox: https://ws.sandbox.pagseguro.uol.com.br/v3/transactions/notifications/{code}?email=...&token=...
    # prod:    https://ws.pagseguro.uol.com.br/v3/transactions/notifications/{code}?email=...&token=...
    base = "https://ws.sandbox.pagseguro.uol.com.br" if _env() == "sandbox" else "https://ws.pagseguro.uol.com.br"
    email, token = _credentials()
    return f"{base}/v3/transactions/notifications/{notification_code}?email={email}&token={token}"


def fetch_transaction_by_notification(notification_code: str) -> dict:
    """
    Retorna dict com campos principais:
      - reference (ex: purchase-123)
      - status (int)
      - code (transaction code)
    """
    url = transactions_notification_url(notification_code)
    r = requests.get(url, timeout=30)
    if not r.ok:
        raise RuntimeError(f"PagSeguro notify erro {r.status_code}: {r.text}")

    # XML -> dict mínimo
    root = ET.fromstring(r.text.strip())
    get = lambda tag: (root.findtext(tag) or "").strip()

    data = {
        "reference": get("reference"),
        "status": get("status"),
        "code": get("code"),
    }
    return data


def _finalize_purchase(purchase: Purchase):
    """
    Usa o finalize já acoplado no app (current_app.extensions['finalize_purchase'])
    do jeito que você já está tentando fazer no routes/webhooks.py.
    """
    finalize = current_app.extensions.get("finalize_purchase")
    if callable(finalize):
        try:
            finalize(purchase.id)
        except TypeError:
            finalize(purchase)


@bp_pagseguro_notify.post("/webhooks/pagseguro")
def pagseguro_notification():
    """
    PagSeguro Classic: recebe notificationType/notificationCode via form POST.
    Confirmação REAL vem de consultar a API com notificationCode.
    """
    payload = request.form or request.json or {}

    notification_code = (payload.get("notificationCode") or "").strip()
    notification_type = (payload.get("notificationType") or "").strip().lower()

    if not notification_code:
        abort(400)

    # geralmente é "transaction"
    if notification_type and notification_type != "transaction":
        return {"ok": True}

    tx = fetch_transaction_by_notification(notification_code)

    reference = (tx.get("reference") or "").strip()  # ex: purchase-123
    status = int(tx.get("status") or 0)
    tx_code = (tx.get("code") or "").strip()

    # pago: 3 (Paga) ou 4 (Disponível)
    if status not in (3, 4):
        return {"ok": True}

    # extrai purchase_id do reference
    purchase_id = None
    if reference.startswith("purchase-"):
        try:
            purchase_id = int(reference.split("-", 1)[1])
        except Exception:
            purchase_id = None

    if not purchase_id:
        abort(400)

    with db() as s:
        purchase = s.get(Purchase, purchase_id)
        if not purchase:
            abort(404)

        # pega o payment pagseguro mais recente desta compra
        payment = s.scalar(
            select(Payment)
            .where(Payment.purchase_id == purchase.id, Payment.provider == "pagseguro")
            .order_by(Payment.id.desc())
        )

        if not payment:
            # cria registro mínimo se não existir
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

        # idempotência
        if payment.status == "paid" and purchase.status == "paid":
            return {"ok": True}

        payment.status = "paid"
        payment.paid_at = datetime.utcnow()
        if tx_code:
            payment.external_id = tx_code  # útil para auditoria

        purchase.status = "paid"

    # finaliza fora do commit (se der erro, não desfaz o "paid")
    _finalize_purchase(purchase)

    return {"ok": True}
