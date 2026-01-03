import base64
import hashlib
import os
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

import requests


def pagbank_base_url() -> str:
    env = (os.getenv("PAGBANK_ENV", "sandbox") or "sandbox").lower().strip()
    return "https://sandbox.api.pagseguro.com" if env == "sandbox" else "https://api.pagseguro.com"


def _auth_headers() -> dict:
    token = os.getenv("PAGBANK_TOKEN", "").strip()
    if not token:
        raise RuntimeError("PAGBANK_TOKEN não configurado no .env")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _normalize_tax_id(buyer_tax_id: str) -> str:
    env = (os.getenv("PAGBANK_ENV", "sandbox") or "sandbox").lower().strip()
    digits = "".join(c for c in (buyer_tax_id or "") if c.isdigit())

    if env == "sandbox":
        if len(digits) not in (11, 14):
            return "12345678909"  # CPF de exemplo aceito
        return digits

    return digits[:14]


def _normalize_buyer_email(buyer_email: str) -> str:
    """
    PagBank exige que o e-mail do comprador seja DIFERENTE do e-mail do lojista.
    Em sandbox, se vier vazio ou igual ao e-mail da conta, usamos um fake válido.
    """
    env = (os.getenv("PAGBANK_ENV", "sandbox") or "sandbox").lower().strip()
    merchant_email = (os.getenv("PAGBANK_MERCHANT_EMAIL") or "").lower().strip()
    email = (buyer_email or "").lower().strip()

    if env == "sandbox":
        if not email or (merchant_email and email == merchant_email):
            return "comprador-teste@exemplo.com"
        return email

    # produção: retorna o informado (ideal validar no form)
    return email


def create_pix_order(
    *,
    reference_id: str,
    buyer_name: str,
    buyer_email: str,
    buyer_tax_id: str,
    buyer_phone_digits: str,
    item_name: str,
    amount_cents: int,
    notification_url: str,
    expires_minutes: int = 30,
) -> Tuple[str, str, str, Optional[datetime]]:

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=expires_minutes)
    expiration_date = expires_at.astimezone().isoformat(timespec="seconds")

    # telefone
    digits = "".join(c for c in (buyer_phone_digits or "") if c.isdigit())
    area = digits[0:2] if len(digits) >= 10 else "31"
    number = digits[2:11] if len(digits) >= 10 else digits[-9:] if len(digits) >= 9 else "999999999"

    tax_id = _normalize_tax_id(buyer_tax_id)
    email = _normalize_buyer_email(buyer_email)

    payload = {
        "reference_id": reference_id,
        "customer": {
            "name": buyer_name[:100],
            "email": email[:150],
            "tax_id": tax_id,
            "phones": [
                {
                    "country": "55",
                    "area": area,
                    "number": number,
                    "type": "MOBILE",
                }
            ],
        },
        "items": [
            {
                "name": item_name[:100],
                "quantity": 1,
                "unit_amount": int(amount_cents),
            }
        ],
        "qr_codes": [
            {
                "amount": {"value": int(amount_cents)},
                "expiration_date": expiration_date,
            }
        ],
        "notification_urls": [notification_url],
    }

    url = f"{pagbank_base_url()}/orders"
    r = requests.post(url, json=payload, headers=_auth_headers(), timeout=30)

    if not r.ok:
        raise RuntimeError(f"PagBank erro {r.status_code}: {r.text}")

    data = r.json()

    order_id = data.get("id")
    if not order_id:
        raise RuntimeError(f"PagBank não retornou 'id'. Resposta: {data}")

    qr_text = ""
    qr_image_b64 = ""

    try:
        qrs = data.get("qr_codes") or []
        links = (qrs[0] or {}).get("links") or []

        text_url = next((x["href"] for x in links if x.get("media") == "text/plain"), None)
        img_url = next((x["href"] for x in links if x.get("media") == "image/png"), None)

        if text_url:
            tr = requests.get(text_url, headers=_auth_headers(), timeout=30)
            tr.raise_for_status()
            qr_text = tr.text.strip()

        if img_url:
            ir = requests.get(img_url, headers=_auth_headers(), timeout=30)
            ir.raise_for_status()
            qr_image_b64 = base64.b64encode(ir.content).decode("ascii")

    except Exception:
        pass

    return order_id, qr_text, qr_image_b64, expires_at


def get_order(*, order_id: str) -> dict:
    url = f"{pagbank_base_url()}/orders/{order_id}"
    r = requests.get(url, headers=_auth_headers(), timeout=30)
    r.raise_for_status()
    return r.json()


def verify_webhook_signature(*, raw_body: bytes, received_signature: str) -> bool:
    token = os.getenv("PAGBANK_WEBHOOK_TOKEN", "").strip()
    if not token or not received_signature:
        return False

    msg = (token + "-").encode("utf-8") + raw_body
    expected = hashlib.sha256(msg).hexdigest()
    return expected.lower() == received_signature.lower()
