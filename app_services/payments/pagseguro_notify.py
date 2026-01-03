# app_services/payments/pagseguro_notify.py
from flask import Blueprint, request, abort
from datetime import datetime
from sqlalchemy import select

from db import db
from models import Payment, Purchase
from app_services.finalize_purchase import finalize_purchase

bp_pagseguro_notify = Blueprint("pagseguro_notify", __name__)


@bp_pagseguro_notify.post("/webhooks/pagseguro")
def pagseguro_notification():
    """
    Notificação oficial PagSeguro (server-to-server)
    Essa é a ÚNICA fonte confiável de confirmação de pagamento.
    """

    payload = request.form or request.json or {}

    notification_type = payload.get("notificationType")
    notification_code = payload.get("notificationCode")

    if not notification_code:
        abort(400)

    # 👉 aqui você pode (depois) consultar a API do PagSeguro
    # para validar o status real do pagamento
    # por enquanto, assumimos pagamento confirmado

    with db() as s:
        payment = s.scalar(
            select(Payment)
            .where(Payment.external_id == notification_code)
        )

        if not payment:
            # PagSeguro às vezes envia notificações repetidas
            return {"ok": True}

        # 🔒 idempotência: não processa duas vezes
        if payment.status == "paid":
            return {"ok": True}

        payment.status = "paid"
        payment.paid_at = datetime.utcnow()

        purchase = s.get(Purchase, payment.purchase_id)
        if purchase:
            purchase.status = "paid"

        s.commit()

        # ✅ FINALIZA A COMPRA (gera ingressos, QR, etc.)
        if purchase:
            finalize_purchase(purchase.id)

    return {"ok": True}
