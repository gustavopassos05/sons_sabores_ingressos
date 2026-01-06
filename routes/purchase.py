# routes/purchase.py
import os
import secrets
from datetime import datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from sqlalchemy import select, desc, and_

from db import db
from models import Event, Purchase, Payment
from app_services.payments.pagbank_checkout import create_checkout_redirect

bp_purchase = Blueprint("purchase", __name__)

def _digits(s: str) -> str:
    return "".join(c for c in (s or "") if c.isdigit())

def _cpf_allowed(cpf_digits: str) -> bool:
    """
    CPF_ALLOWLIST="11122233344,55566677788"
    Se vazio/não configurado -> libera geral (mas você quer travar, então configure).
    """
    allow_raw = (os.getenv("CPF_ALLOWLIST") or "").strip()
    if not allow_raw:
        return True
    allow = {_digits(x) for x in allow_raw.split(",") if _digits(x)}
    return cpf_digits in allow

def _is_expired_payment(p: Payment) -> bool:
    # regra simples: status failed/expired/canceled
    st = (p.status or "").lower().strip()
    return st in {"failed", "expired", "canceled", "cancelled"}

@bp_purchase.get("/buy/<event_slug>")
def buy(event_slug: str):
    with db() as s:
        ev = s.scalar(select(Event).where(Event.slug == event_slug))
        if not ev:
            abort(404)

    return render_template(
        "buy.html",
        event=ev,
        app_name=os.getenv("APP_NAME", "Sons & Sabores"),
        form={},  # mantém
    )

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
    if not cpf_digits or len(cpf_digits) < 11:
        flash("CPF inválido.", "error")
        return redirect(url_for("purchase.buy", event_slug=event_slug))

    # ✅ trava allowlist (soft launch)
    if not _cpf_allowed(cpf_digits):
        flash("Este CPF ainda não está liberado para compra.", "error")
        return redirect(url_for("purchase.buy", event_slug=event_slug))

    guests_raw = (request.form.get("guests_text") or "").strip()
    guests_lines = [x.strip() for x in guests_raw.splitlines() if x.strip()]
    guests_text = "\n".join(guests_lines)

    price_cents_unit = int(os.getenv("TICKET_PRICE_CENTS", "100"))  # ✅ R$ 1,00 por padrão aqui
    total_people = 1 + len(guests_lines)
    total_cents = price_cents_unit * total_people
    total_brl = total_cents / 100

    purchase_token = secrets.token_urlsafe(24)

    with db() as s:
        ev = s.scalar(select(Event).where(Event.slug == event_slug))
        if not ev:
            abort(404)

        # =========================================================
        # ✅ REGRA ANTI DUPLICIDADE (event + show + cpf_digits)
        # =========================================================
        existing_purchase = s.scalar(
            select(Purchase)
            .where(
                and_(
                    Purchase.event_id == ev.id,
                    Purchase.show_name == show_name,
                    Purchase.buyer_cpf_digits == cpf_digits,
                )
            )
            .order_by(desc(Purchase.id))
        )

        if existing_purchase:
            # pega payment pago, senão pega o último
            payment_paid = s.scalar(
                select(Payment)
                .where(Payment.purchase_id == existing_purchase.id, Payment.status == "paid")
                .order_by(desc(Payment.id))
            )
            last_payment = payment_paid or s.scalar(
                select(Payment)
                .where(Payment.purchase_id == existing_purchase.id)
                .order_by(desc(Payment.id))
            )

            # ✅ se já está pago -> ingressos
            if (existing_purchase.status or "").lower() == "paid" or (payment_paid and payment_paid.status == "paid"):
                return redirect(url_for("tickets.purchase_public", token=existing_purchase.token))

            # ✅ se tem pendente -> reabre o checkout existente
            if last_payment and (last_payment.status or "").lower() == "pending" and getattr(last_payment, "checkout_url", None):
                return redirect(last_payment.checkout_url)

            # ✅ se falhou/expirou -> deixa criar nova (segue abaixo)
            # (não faz nada aqui)

        # =========================================================
        # ✅ CRIA NOVA PURCHASE + CHECKOUT (sempre PagBank)
        # =========================================================
        purchase = Purchase(
            event_id=ev.id,
            token=purchase_token,
            show_name=show_name,
            buyer_name=buyer_name,
            buyer_cpf=buyer_cpf,
            buyer_cpf_digits=cpf_digits,   # ✅ NORMALIZADO
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
            amount_brl=total_brl,
            buyer_name=buyer_name,
            buyer_email=buyer_email,
            buyer_phone=buyer_phone,
            buyer_cpf=buyer_cpf,
            redirect_url=redirect_url,
            payment_notification_url=checkout_notification_url,
            checkout_notification_url=None,  # opcional
        )

        payment = Payment(
            purchase_id=purchase.id,
            provider="pagbank_checkout",
            amount_cents=total_cents,
            currency="BRL",
            status="pending",
            external_id=checkout_id,
            checkout_url=checkout_url,  # ✅ guarda pra reabrir
            paid_at=None,
        )
        s.add(payment)
        s.commit()

        return redirect(checkout_url)
