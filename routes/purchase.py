# routes/purchase.py
import os
import secrets
from datetime import datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for, current_app
from sqlalchemy import select, desc, and_

from db import db
from models import Event, Purchase, Payment, Ticket
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

    cpf_digits = "".join(c for c in buyer_cpf if c.isdigit())

    guests_raw = (request.form.get("guests_text") or "").strip()
    guests_lines = [x.strip() for x in guests_raw.splitlines() if x.strip()]
    guests_text = "\n".join(guests_lines)

    # preço (você pediu 1,00)
    total_people = 1 + len(guests_lines)
    total_cents = 100 * total_people
    total_brl = total_cents / 100

    purchase_token = secrets.token_urlsafe(24)

    checkout_notification_url = f"{base_url}/webhooks/pagbank-checkout"
    redirect_url = f"{base_url}/pay/return/{purchase_token}"

    with db() as s:
        ev = s.scalar(select(Event).where(Event.slug == event_slug))
        if not ev:
            abort(404)

        # 🔒 REGRA: se já existe compra para esse CPF+show+evento
        # - se pendente: reaproveita
        # - se paga: manda pra ingressos
        existing = None
        if cpf_digits:
            existing = s.scalar(
                select(Purchase)
                .where(
                    Purchase.event_id == ev.id,
                    Purchase.show_name == show_name,
                    Purchase.buyer_cpf_digits == cpf_digits,
                )
                .order_by(Purchase.id.desc())
            )

        if existing:
            last_payment_paid = s.scalar(
                select(Payment)
                .where(Payment.purchase_id == existing.id, Payment.status == "paid")
                .order_by(Payment.id.desc())
            )
            if last_payment_paid:
                return redirect(url_for("tickets.purchase_public", token=existing.token))

            last_payment = s.scalar(
                select(Payment)
                .where(Payment.purchase_id == existing.id)
                .order_by(Payment.id.desc())
            )

            # pendente com checkout_url → reabre
            if last_payment and (last_payment.status or "").lower() == "pending" and getattr(last_payment, "checkout_url", None):
                return redirect(last_payment.checkout_url)

            # se falhou/expirou → deixa criar nova
            # cai e cria nova compra

        purchase = Purchase(
            event_id=ev.id,
            token=purchase_token,
            show_name=show_name,
            buyer_name=buyer_name,
            buyer_cpf=buyer_cpf,
            buyer_cpf_digits=cpf_digits if cpf_digits else None,
            buyer_email=buyer_email,
            buyer_phone=buyer_phone,
            guests_text=guests_text,
            status="pending_payment",
            created_at=datetime.utcnow(),
        )
        s.add(purchase)
        s.commit()

        # ✅ cria checkout redirect (PagBank decide PIX ou cartão lá)
        checkout_id, checkout_url = create_checkout_redirect(
            reference=f"purchase-{purchase.id}",
            item_description=f"Sons & Sabores - {show_name} ({total_people} ingresso(s))",
            amount_brl=total_brl,
            buyer_name=buyer_name,
            buyer_email=buyer_email,
            buyer_phone=buyer_phone,
            buyer_cpf=buyer_cpf,
            redirect_url=f"{base_url}/pay/return/{purchase.token}",
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
            checkout_url=checkout_url,   # ✅ precisa existir na model + coluna
            paid_at=None,
        )
        s.add(payment)
        s.commit()

        return redirect(checkout_url)

@bp_purchase.get("/pay/return/<token>")
def pay_return(token: str):
    finalize_fn = current_app.extensions.get("finalize_purchase")

    with db() as s:
        purchase = s.scalar(select(Purchase).where(Purchase.token == token))
        if not purchase:
            abort(404)

        payment_paid = s.scalar(
            select(Payment)
            .where(Payment.purchase_id == purchase.id, Payment.status == "paid")
            .order_by(Payment.id.desc())
        )

        payment = payment_paid or s.scalar(
            select(Payment)
            .where(Payment.purchase_id == purchase.id)
            .order_by(Payment.id.desc())
        )

        tickets = list(
            s.scalars(
                select(Ticket)
                .where(Ticket.purchase_id == purchase.id)
                .order_by(Ticket.id.asc())
            )
        )

        should_finalize = (
            payment
            and (payment.status or "").lower() == "paid"
            and not (payment.tickets_pdf_url or payment.tickets_zip_url)
            and callable(finalize_fn)
        )

    if should_finalize:
        try:
            finalize_fn(purchase.id)
        except Exception:
            pass

        with db() as s:
            purchase = s.scalar(select(Purchase).where(Purchase.token == token))
            tickets = list(
                s.scalars(
                    select(Ticket)
                    .where(Ticket.purchase_id == purchase.id)
                    .order_by(Ticket.id.asc())
                )
            )
            payment = s.scalar(
                select(Payment)
                .where(Payment.purchase_id == purchase.id, Payment.status == "paid")
                .order_by(Payment.id.desc())
            ) or s.scalar(
                select(Payment)
                .where(Payment.purchase_id == purchase.id)
                .order_by(Payment.id.desc())
            )

    return render_template(
        "payment_return.html",
        purchase=purchase,
        payment=payment,
        tickets=tickets,
    )

@bp_purchase.get("/pay/<int:payment_id>")
def pay_pix(payment_id: int):
    with db() as s:
        p = s.get(Payment, payment_id)
        if not p:
            abort(404)
    if getattr(p, "checkout_url", None):
        return redirect(p.checkout_url)
    abort(404)
