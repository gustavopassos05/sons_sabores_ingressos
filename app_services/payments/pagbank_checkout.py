# app_services/payments/pagbank_checkout.py
import os
from typing import Optional, Tuple

import requests


def _env() -> str:
    return (os.getenv("PAGBANK_ENV", "sandbox") or "sandbox").lower().strip()


def _base_url() -> str:
    # sandbox: https://sandbox.api.pagseguro.com
    # prod:    https://api.pagseguro.com
    return "https://sandbox.api.pagseguro.com" if _env() == "sandbox" else "https://api.pagseguro.com"


def _auth_headers() -> dict:
    token = (os.getenv("PAGBANK_TOKEN") or "").strip()
    if not token:
        raise RuntimeError("PAGBANK_TOKEN não configurado nas env vars.")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _digits(s: str) -> str:
    return "".join(c for c in (s or "") if c.isdigit())


def _normalize_email(buyer_email: str) -> str:
    # Sandbox costuma ser mais permissivo, mas mantenha válido
    e = (buyer_email or "").strip().lower()
    return e or "comprador-teste@exemplo.com"


def _normalize_tax_id(cpf: str) -> str:
    d = _digits(cpf)
    if _env() == "sandbox":
        return d if len(d) in (11, 14) else "12345678909"
    return d[:14]


def create_checkout_redirect(
    *,
    reference_id: str,
    item_description: str,
    amount_cents: int,
    buyer_name: str,
    buyer_email: str,
    buyer_phone: str,
    buyer_cpf: str,
    redirect_url: str,
    notification_url: Optional[str] = None,
) -> Tuple[str, str]:
    """
    PagBank Checkout:
      POST /checkouts
    Retorna:
      (checkout_id, redirect_href)
    """

    phone_digits = _digits(buyer_phone)
    area = phone_digits[:2] if len(phone_digits) >= 10 else "31"
    number = phone_digits[2:11] if len(phone_digits) >= 10 else (phone_digits[-9:] if len(phone_digits) >= 9 else "999999999")

    payload = {
        "reference_id": reference_id,
        "redirect_url": redirect_url,  # campo existe no Objeto Checkout :contentReference[oaicite:1]{index=1}
        "customer": {
            "name": (buyer_name or "")[:140],
            "email": _normalize_email(buyer_email)[:140],
            "tax_id": _normalize_tax_id(buyer_cpf),
            "phones": [
                {"country": "55", "area": area, "number": number, "type": "MOBILE"}
            ],
        },
        "items": [
            {
                "name": item_description[:100],
                "quantity": 1,
                "unit_amount": int(amount_cents),
            }
        ],
        # Deixa o Checkout oferecer as opções (Pix/Cartão) na página do PagBank
        # (o catálogo de payment_methods está no Objeto Checkout) :contentReference[oaicite:2]{index=2}
        "payment_methods": [
            {"type": "PIX"},
            {"type": "CREDIT_CARD"},
        ],
    }

    if notification_url:
        # no modelo do PagBank, isso pode existir como lista (conforme guia/webhooks),
        # aqui mantemos compatível e simples:
        payload["notification_urls"] = [notification_url]

    url = f"{_base_url()}/checkouts"  # endpoint “Criar Checkout” :contentReference[oaicite:3]{index=3}
    r = requests.post(url, json=payload, headers=_auth_headers(), timeout=30)
    if not r.ok:
        raise RuntimeError(f"PagBank Checkout erro {r.status_code}: {r.text}")

    data = r.json()
    checkout_id = (data.get("id") or "").strip()

    # tenta achar um link de redirecionamento
    redirect_href = ""
    links = data.get("links") or []
    # pega o primeiro link que pareça “redirect”
    for lk in links:
        rel = (lk.get("rel") or "").lower()
        href = (lk.get("href") or "").strip()
        if href and ("redirect" in rel or "pay" in rel):
            redirect_href = href
            break

    # fallback: alguns retornos trazem diretamente uma url
    if not redirect_href:
        redirect_href = (data.get("redirect_url") or "").strip()

    if not checkout_id or not redirect_href:
        raise RuntimeError(f"PagBank não retornou id/url de checkout. Resposta: {data}")

    return checkout_id, redirect_href
