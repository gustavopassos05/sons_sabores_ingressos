# routes/purchase.py
import os
import secrets
from datetime import datetime

from flask import Blueprint, request, redirect, url_for, flash, abort

from sqlalchemy import select

from models import Event, Purchase, Payment
from app_services.payments.pagseguro import create_checkout_redirect


bp = Blueprint("purchase", __name__)


def _digits(s: str) -> str:
    return "".join(c for c in (s or "") if c.isdigit())


def _has_name_and_surname(full_name: str) -> bool:
    parts = [p for p in (full_name or "").strip().split() if p]
    return len(parts) >= 2


@bp.post("/buy/<event_slug>")
def buy_post(event_slug: str):
    show_name = (request.form.get("show_name") or "").strip()
    if not show_name:
        flash("Selecione o show.", "error")
        return redirect(url_for("buy", event_slug=event_slug))

    buyer_name = (request.form.get("buyer_name") or "").strip()
    if not buyer_name:
        flash("Nome do comprador é obrigatório.", "error")
        return redirect(url_for("buy", event_slug=event_slug))

    if not _has_name_and_surname(buyer_name):
        flash("Informe nome e sobrenome do comprador.", "error")
        return redirect(url_for("buy", event_slug=event_slug))

    buyer_cpf = (request.form.get("buyer_cpf") or "").strip()
    buyer_email = (request.form.get("buyer_email") or "").strip()
    buyer_phone = (request.form.get("buyer_phone") or "").strip()

    guests_raw = (request.form.get("guests_text") or "").strip()
    guests_lines = [x.strip() for x in guests_raw.splitlines() if x.strip()]

    # valida acompanhantes: nome + sobrenome
    for g in guests_lines:
        if not _has_name_and_surname(g):
            flash(f"Acompanhante precisa ter nome e sobrenome: “{g}”.", "error")
            return redirect(url_for("buy", event_slug=event_slug))

    guests_text = "\n".join(guests_lines)

    # ✅ cobra por pessoa
    unit_price_cents = int(os.getenv("TICKET_PRICE_CENTS", "5000"))
    total_people = 1 + len(guests_lines)
    total_cents = unit_price_cents * total_people
    total_brl = total_cents / 100.0

    base_url = (os.getenv("BASE_URL") or "http://127.0.0.1:5005").rstrip("/")

    # token da compra (UNIQUE + NOT NULL)
    purchase_token = secrets.token_urlsafe(24)

    # cpf só dígitos pro PagSeguro
    buyer_tax_id_digits = _digits(buyer_cpf)

    from app import db  # usa o helper do seu create_app
    with db() as s:
        ev = s.scalar(select(Event).where(Event.slug == event_slug))
        if not ev:
            abort(404)

        purchase = Purchase(
            event_id=ev.id,
            token=purchase_token,
            show_name=show_name,
            buyer_name=buyer_name,
            buyer_cpf=buyer_cpf,
            buyer_email=buyer_email,
            buyer_phone=buyer_phone,
            guests_text=guests_text,
            status="pending_payment",
            created_at=datetime.utcnow(),
        )
        s.add(purchase)
        s.commit()

        # cria checkout redirect (cartão + pix etc dentro do PagSeguro)
        notify_url = f"{base_url}/webhooks/pagseguro"  # (se você quiser implementar notificação)
        redirect_back = f"{base_url}/purchase/{purchase.token}"

        checkout_code, pay_url = create_checkout_redirect(
            reference=f"purchase-{purchase.id}",
            item_description=f"Sons & Sabores - {show_name} ({total_people} pessoa(s))",
            amount_brl=total_brl,
            buyer_name=buyer_name,
            buyer_email=buyer_email,
            buyer_phone=buyer_phone,
            buyer_cpf=buyer_tax_id_digits,
            redirect_url=redirect_back,
            notification_url=notify_url,
        )

        payment = Payment(
            purchase_id=purchase.id,
            provider="pagseguro_redirect",
            amount_cents=total_cents,
            currency="BRL",
            status="pending",
            external_id=checkout_code,  # guarda o code
            qr_text="",
            qr_image_base64="",
            expires_at=None,
            paid_at=None,
        )
        s.add(payment)
        s.commit()

    # ✅ manda o cliente pro PagSeguro (redirect)
    return redirect(pay_url)
