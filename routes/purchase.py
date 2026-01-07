# routes/purchase.py
import os
import secrets
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from sqlalchemy import select, and_

from db import db
from models import Event, Purchase, Payment

from app_services.payments.pagbank_checkout import create_checkout_redirect

bp_purchase = Blueprint("purchase", __name__)


def _digits(s: str) -> str:
    return "".join(c for c in (s or "") if c.isdigit())


def _brl_from_cents(cents: int) -> str:
    v = (Decimal(cents) / Decimal(100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return format(v, "f")  # "1.00", "3.00", etc


def _is_payment_open(p: Payment) -> bool:
    # Ajuste se você tiver outros status "abertos"
    return (p.status or "").lower() in {"pending", "pending_payment", "waiting_payment", "in_process"}

@bp_purchase.post("/buy/<event_slug>")
def buy_post(event_slug: str):
    base_url = (os.getenv("BASE_URL") or "").strip().rstrip("/")
    if not base_url:
        raise RuntimeError("BASE_URL não configurado")

    show_name = (request.form.get("show_name") or "").strip()
    buyer_name = (request.form.get("buyer_name") or "").strip()
    buyer_cpf = (request.form.get("buyer_cpf") or "").strip()
    buyer_email = (request.form.get("buyer_email") or "").strip()
    buyer_phone = (request.form.get("buyer_phone") or "").strip()

    if not show_name:
        flash("Selecione o show.", "error")
        return redirect(url_for("purchase.buy", event_slug=event_slug))

    if not buyer_name:
        flash("Nome do comprador é obrigatório.", "error")
        return redirect(url_for("purchase.buy", event_slug=event_slug))

    cpf_digits = _digits(buyer_cpf)
    if not cpf_digits:
        flash("CPF é obrigatório.", "error")
        return redirect(url_for("purchase.buy", event_slug=event_slug))

    # ✅ convidados: aceita linhas OU separados por vírgula/;
    guests_raw = (request.form.get("guests_text") or "").strip()

    # quebra por linha primeiro
    tmp = []
    for line in guests_raw.splitlines():
        line = line.strip()
        if not line:
            continue
        # se a pessoa colou "a, b; c"
        parts = [p.strip() for p in line.replace(";", ",").split(",") if p.strip()]
        tmp.extend(parts)

    guests_lines = tmp
    guests_text = "\n".join(guests_lines)

    price_cents_unit = int(os.getenv("TICKET_PRICE_CENTS", "100"))  # 1,00
    total_people = 1 + len(guests_lines)  # ✅ inclui comprador sempre
    total_cents = price_cents_unit * total_people
    total_brl_str = _brl_from_cents(total_cents)

    purchase_token = secrets.token_urlsafe(24)

    with db() as s:
        ev = s.scalar(select(Event).where(Event.slug == event_slug))
        if not ev:
            abort(404)

        existing_purchase = s.scalar(
            select(Purchase)
            .where(
                Purchase.event_id == ev.id,
                Purchase.show_name == show_name,
                Purchase.buyer_cpf_digits == cpf_digits,
            )
            .order_by(Purchase.id.desc())
        )

        if existing_purchase:
            existing_payment = s.scalar(
                select(Payment)
                .where(Payment.purchase_id == existing_purchase.id)
                .order_by(Payment.id.desc())
            )

            # pago -> ingressos
            if (existing_purchase.status or "").lower() == "paid" or (
                existing_payment and (existing_payment.status or "").lower() == "paid"
            ):
                return redirect(url_for("tickets.purchase_public", token=existing_purchase.token))

            # ✅ pendente -> reabrir SOMENTE se a lista de convidados for igual
            # se mudou, cria nova compra (pra não reutilizar valor antigo)
            if existing_payment and _is_payment_open(existing_payment):
                same_guests = (existing_purchase.guests_text or "").strip() == guests_text.strip()
                if same_guests and getattr(existing_payment, "checkout_url", None):
                    return redirect(existing_payment.checkout_url)

                # se mudou convidados, deixa criar nova

        # cria nova compra
        purchase = Purchase(
            event_id=ev.id,
            token=purchase_token,
            show_name=show_name,
            buyer_name=buyer_name,
            buyer_cpf=buyer_cpf,
            buyer_cpf_digits=cpf_digits,
            buyer_email=buyer_email,
            buyer_phone=buyer_phone,
            guests_text=guests_text,
            status="pending_payment",
            created_at=datetime.utcnow(),
        )
        s.add(purchase)
        s.commit()

        checkout_notification_url = f"{base_url}/webhooks/pagbank-checkout"
        redirect_url = f"{base_url}/pay/return/{purchase.token}"

        checkout_id, checkout_url = create_checkout_redirect(
            reference=f"purchase-{purchase.id}",
            item_description=f"Sons & Sabores - {show_name} ({total_people} ingresso(s))",
            amount_brl=total_brl_str,
            buyer_name=buyer_name,
            buyer_email=buyer_email,
            buyer_phone=buyer_phone,
            buyer_cpf=buyer_cpf,
            redirect_url=redirect_url,
            payment_notification_url=checkout_notification_url,
            checkout_notification_url=None,
        )

        payment = Payment(
            purchase_id=purchase.id,
            provider="pagbank_checkout",
            amount_cents=total_cents,
            currency="BRL",
            status="pending",
            external_id=checkout_id,
            checkout_url=checkout_url,
            paid_at=None,
        )
        s.add(payment)
        s.commit()

        return redirect(checkout_url)
