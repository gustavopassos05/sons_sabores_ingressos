# app_services/payments/pagseguro.py
import os
import xml.etree.ElementTree as ET
from typing import Tuple, Optional

import requests


def _env() -> str:
    # sandbox | production
    return (os.getenv("PAGSEGURO_ENV", "sandbox") or "sandbox").lower().strip()


def checkout_post_url() -> str:
    # Provider Redirect - "Obter autorização - Código Checkout"
    # sandbox: https://ws.sandbox.pagseguro.uol.com.br/v2/checkout
    # prod:    https://ws.pagseguro.uol.com.br/v2/checkout
    return "https://ws.sandbox.pagseguro.uol.com.br/v2/checkout" if _env() == "sandbox" else "https://ws.pagseguro.uol.com.br/v2/checkout"


def checkout_redirect_base() -> str:
    # Link de Redirecionamento:
    # sandbox: https://sandbox.pagseguro.uol.com.br/v2/checkout/payment.html?code=...
    # prod:    https://pagseguro.uol.com.br/v2/checkout/payment.html?code=...
    return "https://sandbox.pagseguro.uol.com.br/v2/checkout/payment.html" if _env() == "sandbox" else "https://pagseguro.uol.com.br/v2/checkout/payment.html"


def _credentials() -> Tuple[str, str]:
    email = (os.getenv("PAGBANK_MERCHANT_EMAIL") or "").strip()
    token = (os.getenv("PAGBANK_TOKEN") or "").strip()
    if not email or not token:
        raise RuntimeError("PAGBANK_MERCHANT_EMAIL e PAGBANK_TOKEN precisam estar no .env")
    return email, token


def _digits(s: str) -> str:
    return "".join(c for c in (s or "") if c.isdigit())


def create_checkout_redirect(
    *,
    reference: str,
    item_description: str,
    amount_brl: float,
    buyer_name: str,
    buyer_email: str,
    buyer_phone: str,
    buyer_cpf: str,
    redirect_url: str,
    notification_url: Optional[str] = None,
) -> Tuple[str, str]:
    """
    Retorna:
      (checkout_code, redirect_url_pagseguro)
    """
    email, token = _credentials()

    phone_digits = _digits(buyer_phone)
    area = phone_digits[:2] if len(phone_digits) >= 10 else "31"
    number = phone_digits[2:11] if len(phone_digits) >= 10 else (phone_digits[-9:] if len(phone_digits) >= 9 else "999999999")

    cpf_digits = _digits(buyer_cpf)
    if _env() == "sandbox" and len(cpf_digits) != 11:
        cpf_digits = "12345678909"  # sandbox costuma aceitar CPF de exemplo

    # IMPORTANTE: PagSeguro Redirect é um POST FORM URLENCODED
    payload = {
        "email": email,
        "token": token,
        "currency": "BRL",
        "reference": reference[:200],
        "itemId1": "1",
        "itemDescription1": item_description[:100],
        "itemQuantity1": "1",
        "itemAmount1": f"{amount_brl:.2f}",
        "senderName": buyer_name[:50],
        "senderEmail": (buyer_email or "").strip()[:60] or "comprador-teste@exemplo.com",
        "senderAreaCode": area,
        "senderPhone": number,
        "senderCPF": cpf_digits,
        "redirectURL": redirect_url,
    }
    if notification_url:
        payload["notificationURL"] = notification_url

    r = requests.post(checkout_post_url(), data=payload, timeout=30)
    if not r.ok:
        raise RuntimeError(f"PagSeguro erro {r.status_code}: {r.text}")

    # resposta XML: <checkout><code>...</code>...</checkout>
    try:
        root = ET.fromstring(r.text.strip())
        code = (root.findtext("code") or "").strip()
    except Exception:
        code = ""

    if not code:
        raise RuntimeError(f"PagSeguro não retornou code. Resposta: {r.text}")

    url = f"{checkout_redirect_base()}?code={code}"
    return code, url
