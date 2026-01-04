# app_services/payments/pagbank_checkout.py
import os
import requests
from typing import Tuple

def pagbank_base_url() -> str:
    env = (os.getenv("PAGBANK_ENV", "sandbox") or "sandbox").lower().strip()
    return "https://sandbox.api.pagseguro.com" if env == "sandbox" else "https://api.pagseguro.com"

def _auth_headers() -> dict:
    token = (os.getenv("PAGBANK_TOKEN") or "").strip()
    if not token:
        raise RuntimeError("PAGBANK_TOKEN não configurado")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

def create_checkout(
    *,
    reference_id: str,
    buyer_name: str,
    buyer_email: str,
    buyer_tax_id: str,
    buyer_phone: str,
    item_name: str,
    amount_cents: int,
    return_url: str,
    notification_url: str,
) -> Tuple[str, str]:
    """
    Retorna: (checkout_id, checkout_url)
    Checkout do PagBank (Pix + Cartão na mesma página)
    """
    digits_phone = "".join(c for c in (buyer_phone or "") if c.isdigit())
    area = digits_phone[:2] if len(digits_phone) >= 10 else "31"
    number = digits_phone[2:11] if len(digits_phone) >= 10 else (digits_phone[-9:] if len(digits_phone) >= 9 else "999999999")

    tax_id = "".join(c for c in (buyer_tax_id or "") if c.isdigit())
    if not tax_id:
        tax_id = "12345678909"  # sandbox

    email = (buyer_email or "").strip() or "comprador-teste@exemplo.com"

    payload = {
        "reference_id": reference_id,
        "customer": {
            "name": buyer_name[:100],
            "email": email[:150],
            "tax_id": tax_id[:14],
            "phones": [{"country": "55", "area": area, "number": number, "type": "MOBILE"}],
        },
        "items": [{"name": item_name[:100], "quantity": 1, "unit_amount": int(amount_cents)}],
        "payment_methods": [{"type": "CREDIT_CARD"}, {"type": "PIX"}],
        "redirect_url": return_url,
        "notification_urls": [notification_url],
    }

    url = f"{pagbank_base_url()}/checkouts"
    r = requests.post(url, json=payload, headers=_auth_headers(), timeout=30)
    if not r.ok:
        raise RuntimeError(f"PagBank checkout erro {r.status_code}: {r.text}")

    data = r.json()
    checkout_id = (data.get("id") or "").strip()
    checkout_url = ""

    links = data.get("links") or []
    # normalmente vem um link com rel="PAY" ou "REDIRECT"
    for lk in links:
        rel = (lk.get("rel") or "").upper()
        if rel in ("PAY", "REDIRECT", "CHECKOUT", "SELF"):
            checkout_url = lk.get("href") or ""
            if checkout_url:
                break

    if not checkout_id or not checkout_url:
        raise RuntimeError(f"PagBank não retornou link de checkout. Resposta: {data}")

    return checkout_id, checkout_url
