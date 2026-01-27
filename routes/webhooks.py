# Exemplo simples (ajuste conforme seu webhooks.py)
from flask import request

@bp_webhooks.post("/webhooks/mercadopago")
def mp_webhook():
    # Mercado Pago pode mandar eventos diferentes; normalmente você consulta o pagamento/preference
    data = request.get_json(silent=True) or {}
    current_app.logger.info("[MP WEBHOOK] %s", data)

    # TODO: você vai precisar buscar detalhes no MP API (payment/preference)
    # e, ao confirmar approved/paid, setar:
    # payment.status="paid"; payment.paid_at=...; purchase.status="paid"; chamar finalize_purchase
    return {"ok": True}
