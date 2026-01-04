# app_services/payments/pagbank_checkout.py
import os
from typing import Optional, Tuple
import requests


def _env() -> str:
    return (os.getenv("PAGBANK_ENV", "sandbox") or "sandbox").lower().strip()


def checkout_post_url() -> str:
    return (
        "https://sandbox.api.pagseguro.com/checkouts"
        if _env() == "sandbox"
        else "https://api.pagseguro.com/checkouts"
    )


def _bearer_token() -> str:
    token = (os.getenv("PAGBANK_CHECKOUT_TOKEN") or "").strip()
    if not token:
        raise RuntimeError("Configure PAGBANK_CHECKOUT_TOKEN (Bearer) nas env vars do Render.")
    return token


def _digits(s: str) -> str:
    return "".join(c for c in (s or "") if c.isdigit())


def create_checkout_redirect(
    *,
    reference: str,  # ex: "purchase-123"
    item_description: str,
    amount_brl: float,
    buyer_name: str,
    buyer_email: str,
    buyer_phone: str,
    buyer_cpf: str,
    redirect_url: str,
    payment_notification_url: Optional[str] = None,  # <-- transacional (PAID etc.)
    checkout_notification_url: Optional[str] = None,  # <-- checkout (EXPIRED etc.)
) -> Tuple[str, str]:
    token = _bearer_token()

    phone = _digits(buyer_phone)
    ddd = phone[:2] if len(phone) >= 10 else "31"
    number = phone[2:11] if len(phone) >= 10 else (phone[-9:] if len(phone) >= 9 else "999999999")

    cpf = _digits(buyer_cpf)
    if _env() == "sandbox" and len(cpf) != 11:
        cpf = "12345678909"

    unit_amount = int(round(float(amount_brl) * 100))
    if unit_amount <= 0:
        raise RuntimeError("amount_brl inválido (<= 0).")

    payload = {
        "reference_id": reference[:200],
        "customer": {
            "name": buyer_name[:120],
            "email": (buyer_email or "").strip()[:120] or "comprador-teste@exemplo.com",
            "tax_id": cpf,
            "phone": {"country": "+55", "area": ddd, "number": number},
        },
        "items": [
            {"reference_id": "1", "name": item_description[:100], "quantity": 1, "unit_amount": unit_amount}
        ],
        "redirect_url": redirect_url,
    }

    # docs: payment_notification_urls e notification_urls são independentes :contentReference[oaicite:2]{index=2}
    if payment_notification_url:
        payload["payment_notification_urls"] = [payment_notification_url]
    if checkout_notification_url:
        payload["notification_urls"] = [checkout_notification_url]

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    r = requests.post(checkout_post_url(), json=payload, headers=headers, timeout=30)
    if not r.ok:
        raise RuntimeError(f"Checkout Redirect erro {r.status_code}: {r.text}")

    data = r.json()

    pay_url = ""
    for link in data.get("links", []):
        if (link.get("rel") or "").upper() == "PAY" and link.get("href"):
            pay_url = link["href"]
            break

    checkout_id = data.get("id") or ""
    if not checkout_id or not pay_url:
        raise RuntimeError(f"Checkout criado mas resposta inesperada: {data}")

    return checkout_id, pay_url
