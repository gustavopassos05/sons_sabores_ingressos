# routes/webhooks.py
import os
from flask import Blueprint, request, abort

bp_webhooks = Blueprint("webhooks", __name__)

@bp_webhooks.post("/webhooks/pagbank")
def pagbank_webhook():
    # (Opcional) valida um token simples pra evitar spam:
    expected = (os.getenv("PAGBANK_WEBHOOK_TOKEN") or "").strip()
    if expected:
        got = (request.headers.get("X-Webhook-Token") or "").strip()
        if got != expected:
            abort(401)

    data = request.get_json(silent=True) or {}
    # aqui depois a gente processa e marca payment como paid
    # por enquanto só responde 200 pra parar o 404
    return {"ok": True}, 200
