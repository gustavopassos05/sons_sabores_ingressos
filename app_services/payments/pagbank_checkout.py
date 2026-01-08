# app_services/payments/pagbank_checkout.py
import os
import xml.etree.ElementTree as ET
from typing import Optional, Tuple

import requests


def _env() -> str:
    return (os.getenv("PAGBANK_ENV", "sandbox") or "sandbox").lower().strip()


def checkout_post_url() -> str:
    # Checkout clássico (PagSeguro v2)
    return (
        "https://ws.sandbox.pagseguro.uol.com.br/v2/checkout"
        if _env() == "sandbox"
        else "https://ws.pagseguro.uol.com.br/v2/checkout"
    )


def checkout_redirect_base() -> str:
    return (
        "https://sandbox.pagseguro.uol.com.br/v2/checkout/payment.html"
        if _env() == "sandbox"
        else "https://pagseguro.uol.com.br/v2/checkout/payment.html"
    )


def _credentials() -> Tuple[str, str]:
    email = (os.getenv("PAGBANK_CHECKOUT_EMAIL") or "").strip()
    token = (os.getenv("PAGBANK_CHECKOUT_TOKEN") or "").strip()
    if not email or not token:
        raise RuntimeError("Configure PAGBANK_CHECKOUT_EMAIL e PAGBANK_CHECKOUT_TOKEN (clássico) nas env vars do Render.")
    return email, token


def _digits(s: str) -> str:
    return "".join(c for c in (s or "") if c.isdigit())


def create_checkout_redirect(
    *,
    reference: str,  # ex: "purchase-123"
    item_description: str,
    amount_brl: str,  # ✅ agora mande string "X.XX" (seguro)
    buyer_name: str,
    buyer_email: str,
    buyer_phone: str,
    buyer_cpf: str,
    redirect_url: str,
    notification_url: Optional[str] = None,
) -> Tuple[str, str]:
    """
    Checkout Clássico:
    - POST form-urlencoded (email, token, itemAmount1...)
    - resposta XML com <code>
    - redireciona para payment.html?code=<code>
    """
    email, token = _credentials()

    phone_digits = _digits(buyer_phone)
    area = phone_digits[:2] if len(phone_digits) >= 10 else "31"
    number = (
        phone_digits[2:11]
        if len(phone_digits) >= 10
        else (phone_digits[-9:] if len(phone_digits) >= 9 else "999999999")
    )

    cpf_digits = _digits(buyer_cpf)
    if _env() == "sandbox" and len(cpf_digits) != 11:
        cpf_digits = "12345678909"

    # payload clássico (form)
    payload = {
        "email": email,
        "token": token,
        "currency": "BRL",
        "reference": reference[:200],
        "itemId1": "1",
        "itemDescription1": item_description[:100],
        "itemQuantity1": "1",
        "itemAmount1": str(amount_brl),  # "1.00", "3.00"...
        "senderName": buyer_name[:50],
        "senderEmail": (buyer_email or "").strip()[:60] or "comprador-teste@exemplo.com",
        "senderAreaCode": area,
        "senderPhone": number,
        "senderCPF": cpf_digits,
        "redirectURL": redirect_url,
        "charset": "UTF-8",
    }

    if notification_url:
        payload["notificationURL"] = notification_url

    r = requests.post(checkout_post_url(), data=payload, timeout=30)

    if not r.ok:
        raise RuntimeError(f"Checkout Redirect erro {r.status_code}: {r.text}")

    # parse XML
    code = ""
    try:
        root = ET.fromstring(r.text.strip())
        code = (root.findtext("code") or "").strip()
    except Exception:
        code = ""

    if not code:
        raise RuntimeError(f"Checkout Redirect não retornou code. Resposta: {r.text}")

    url = f"{checkout_redirect_base()}?code={code}"
    return code, url
