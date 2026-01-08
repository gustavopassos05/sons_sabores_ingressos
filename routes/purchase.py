# routes/purchase.py
import os
import secrets
from datetime import datetime, timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for, current_app
from sqlalchemy import select

from db import db
from models import Event, Purchase, Payment, Ticket
from pagbank import create_pix_order  # Orders API (PIX)

bp_purchase = Blueprint("purchase", __name__)


def _digits(s: str) -> str:
    return "".join(c for c in (s or "") if c.isdigit())


def _cpf_allowed(cpf_digits: str) -> bool:
    # CPF_ALLOWLIST="11122233344,55566677788"
    raw = (os.getenv("CPF_ALLOWLIST") or "").strip()
    if not raw:
        return True
    allow = {_digits(x) for x in raw.split(",") if _digits(x)}
    return cpf_digits in allow


def _is_open_payment(p: Payment) -> bool:
    return (p.status or "").lower() == "pending"


def _is_expired_payment(p: Payment) -> bool:
    st = (p.status or "").lower()
    if st in {"failed", "expired", "canceled", "cancelled"}:
        return True
    if p.expires_at and p.expires_at < datetime.utcnow():
        return True
    return False


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
        form={},
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
    if not cpf_digits:
        flash("CPF é obrigatório.", "error")
        return redirect(url_for("purchase.buy", event_slug=event_slug))

    # ✅ trava allowlist
    if not _cpf_allowed(cpf_digits):
        flash("Este CPF ainda não está liberado para compra.", "error")
        return redirect(url_for("purchase.buy", event_slug=event_slug))

    # ✅ convidados: linhas OU vírgula/;
    guests_raw = (request.form.get("guests_text") or "").strip()
    tmp = []
    for line in guests_raw.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.replace(";", ",").split(",") if p.strip()]
        tmp.extend(parts)

    guests_lines = tmp
    guests_text = "\n".join(guests_lines)

    # ✅ total por pessoa (comprador + convidados)
    price_cents_unit = int(os.getenv("TICKET_PRICE_CENTS", "100"))  # R$ 1,00
    total_people = 1 + len(guests_lines)
    total_cents = price_cents_unit * total_people

    exp_min = int(os.getenv("PIX_EXP_MINUTES", "30"))
    expires_at_local = datetime.utcnow() + timedelta(minutes=exp_min)

    purchase_token = secrets.token_urlsafe(24)

    with db() as s:
        ev = s.scalar(select(Event).where(Event.slug == event_slug))
        if not ev:
            abort(404)

        # Anti-duplicidade (CPF + show + evento)
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
                .where(Payment.purchase_id == existing_purchase.id, Payment.provider == "pagbank")
                .order_by(Payment.id.desc())
            )

            # pago -> ingressos
            if (existing_purchase.status or "").lower() == "paid" or (
                existing_payment and (existing_payment.status or "").lower() == "paid"
            ):
                return redirect(url_for("tickets.purchase_public", token=existing_purchase.token))

            # pendente -> reaproveita se mesma lista e não expirou
            if existing_payment and _is_open_payment(existing_payment) and not _is_expired_payment(existing_payment):
                same_guests = (existing_purchase.guests_text or "").strip() == guests_text.strip()
                if same_guests:
                    return redirect(url_for("purchase.pay_pix", payment_id=existing_payment.id))
                # mudou convidados -> cria nova

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

        # webhook PIX Orders
        pagbank_notification_url = f"{base_url}/webhooks/pagbank"

        # cria order pix
        order_id, qr_text, qr_b64, expires_at = create_pix_order(
            reference_id=f"purchase-{purchase.id}",
            buyer_name=buyer_name,
            buyer_email=buyer_email,
            buyer_tax_id=cpf_digits,
            buyer_phone_digits=buyer_phone,
            item_name=f"Sons & Sabores - {show_name} ({total_people} ingresso(s))",
            amount_cents=total_cents,
            notification_url=pagbank_notification_url,
            expires_minutes=exp_min,
        )

        payment = Payment(
            purchase_id=purchase.id,
            provider="pagbank",
            amount_cents=total_cents,
            currency="BRL",
            status="pending",
            external_id=order_id,
            qr_text=qr_text,
            qr_image_base64=qr_b64,
            expires_at=(expires_at.replace(tzinfo=None) if expires_at else expires_at_local),
            paid_at=None,
        )
        s.add(payment)
        s.commit()

        return redirect(url_for("purchase.pay_pix", payment_id=payment.id))


@bp_purchase.get("/pay/<int:payment_id>")
def pay_pix(payment_id: int):
    now = datetime.utcnow()

    with db() as s:
        p = s.get(Payment, payment_id)
        if not p:
            abort(404)

        purchase = s.get(Purchase, p.purchase_id) if p.purchase_id else None
        if not purchase:
            abort(404)

        ev = s.get(Event, purchase.event_id)

        # já pago -> vai pros ingressos
        if (purchase.status or "").lower() == "paid" or (p.status or "").lower() == "paid":
            return redirect(url_for("tickets.purchase_public", token=purchase.token))

        # ✅ se expirou, marca failed (pra sua regra de duplicidade deixar criar nova)
        if p.expires_at and p.status == "pending" and p.expires_at < now:
            p.status = "failed"
            purchase.status = "failed"
            s.add(p)
            s.add(purchase)
            s.commit()
            return redirect(url_for("purchase.pay_return", token=purchase.token))
        price_cents_unit = int(os.getenv("TICKET_PRICE_CENTS", "100"))  # R$ 1,00 padrão

    # convidados: usar a MESMA lógica do buy_post
    guests_raw = (purchase.guests_text or "").strip()
    tmp = []
    for line in guests_raw.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.replace(";", ",").split(",") if p.strip()]
        tmp.extend(parts)

    total_people = 1 + len(tmp)


    return render_template(
        "pay_pix.html",
        app_name=os.getenv("APP_NAME", "Sons & Sabores"),
        payment=p,
        purchase=purchase,
        event=ev,
        ticket_price_cents=price_cents_unit,
        total_people=total_people,
    )


@bp_purchase.post("/pay/<int:payment_id>/refresh")
def pay_refresh(payment_id: int):
    return redirect(url_for("purchase.pay_pix", payment_id=payment_id))


@bp_purchase.get("/pay/return/<token>")
def pay_return(token: str):
    finalize_fn = current_app.extensions.get("finalize_purchase")

    def _load():
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
        return purchase, payment, tickets

    purchase, payment, tickets = _load()

    # se pagou mas ainda não gerou links, tenta finalize
    if (
        payment
        and (payment.status or "").lower() == "paid"
        and not (getattr(payment, "tickets_pdf_url", None) or getattr(payment, "tickets_zip_url", None))
        and callable(finalize_fn)
    ):
        try:
            finalize_fn(purchase.id)
        except Exception:
            pass
        purchase, payment, tickets = _load()

    # se pagou, vai pra página final
    if (purchase.status or "").lower() == "paid":
        return redirect(url_for("tickets.purchase_public", token=purchase.token))

    return render_template(
        "payment_return.html",
        purchase=purchase,
        payment=payment,
        tickets=tickets,
        app_name=os.getenv("APP_NAME", "Sons & Sabores"),
    )

