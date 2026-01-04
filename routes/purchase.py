import os
import secrets
from datetime import datetime

from db import db
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from sqlalchemy import select

from models import Event, Purchase, Payment
from pagbank import create_pix_order
from app_services.payments.pagseguro import create_checkout_redirect


bp_purchase = Blueprint("purchase", __name__)


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
    )

@bp_purchase.post("/buy/<event_slug>")
def buy_post(event_slug: str):
    base_url = (os.getenv("BASE_URL") or "").strip().rstrip("/")
    if not base_url:
        raise RuntimeError("BASE_URL não configurado")

    pay_method = (request.form.get("pay_method") or "pix").lower().strip()

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

    guests_raw = (request.form.get("guests_text") or "").strip()
    guests_lines = [x.strip() for x in guests_raw.splitlines() if x.strip()]
    guests_text = "\n".join(guests_lines)

    price_cents_unit = int(os.getenv("TICKET_PRICE_CENTS", "5000"))
    total_people = 1 + len(guests_lines)
    total_cents = price_cents_unit * total_people
    total_brl = total_cents / 100

    exp_min = int(os.getenv("PIX_EXP_MINUTES", "30"))
    purchase_token = secrets.token_urlsafe(24)
    buyer_tax_id_digits = "".join(c for c in buyer_cpf if c.isdigit())

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

        # -----------------------------
        # PIX (PagBank API) -> sua tela pay_pix.html
        # -----------------------------
        if pay_method == "pix":
            notification_url = f"{base_url}/webhooks/pagbank"

            order_id, qr_text, qr_b64, expires_at = create_pix_order(
                reference_id=f"purchase-{purchase.id}",
                buyer_name=buyer_name,
                buyer_email=buyer_email,
                buyer_tax_id=buyer_tax_id_digits,
                buyer_phone_digits=buyer_phone,
                item_name=f"Sons & Sabores - {show_name} ({total_people} ingresso(s))",
                amount_cents=total_cents,
                notification_url=notification_url,
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
                expires_at=expires_at.replace(tzinfo=None) if expires_at else None,
                paid_at=None,
            )
            s.add(payment)
            s.commit()

            return redirect(url_for("purchase.pay_pix", payment_id=payment.id))

        # -----------------------------
        # CARTÃO (Checkout Redirect PagSeguro/PagBank) -> redireciona pra página do provedor
        # -----------------------------
        redirect_url = f"{base_url}/pay/return/{purchase.token}"
        notification_url = f"{base_url}/webhooks/pagseguro"

        checkout_code, checkout_url = create_checkout_redirect(
            reference=f"purchase-{purchase.id}",
            item_description=f"Sons & Sabores - {show_name} ({total_people} ingresso(s))",
            amount_brl=total_brl,
            buyer_name=buyer_name,
            buyer_email=buyer_email,
            buyer_phone=buyer_phone,
            buyer_cpf=buyer_cpf,
            redirect_url=redirect_url,
            notification_url=notification_url,
        )

        payment = Payment(
            purchase_id=purchase.id,
            provider="pagbank_checkout",
            amount_cents=total_cents,
            currency="BRL",
            status="pending",
            external_id=checkout_code,
            paid_at=None,
        )
        s.add(payment)
        s.commit()

    return redirect(checkout_url)


@bp_purchase.get("/pay/<int:payment_id>")
def pay_pix(payment_id: int):
    with db() as s:
        p = s.get(Payment, payment_id)
        if not p:
            abort(404)

        purchase = s.get(Purchase, p.purchase_id) if p.purchase_id else None
        if not purchase:
            abort(404)

        ev = s.get(Event, purchase.event_id)

        if purchase.status == "paid" or p.status == "paid":
            return redirect(url_for("tickets.purchase_public", token=purchase.token))

    return render_template(
        "pay_pix.html",
        app_name=os.getenv("APP_NAME", "Sons & Sabores"),
        payment=p,
        purchase=purchase,
        event=ev,
    )

@bp_purchase.post("/pay/<int:payment_id>/refresh")
def pay_refresh(payment_id: int):
    return redirect(url_for("purchase.pay_pix", payment_id=payment_id))

@bp_purchase.get("/pay/<int:payment_id>/card")
def pay_card(payment_id: int):
    with db() as s:
        p = s.get(Payment, payment_id)
        if not p:
            abort(404)
        purchase = s.get(Purchase, p.purchase_id) if p.purchase_id else None
        if not purchase:
            abort(404)

        if purchase.status == "paid" or p.status == "paid":
            return redirect(url_for("tickets.purchase_public", token=purchase.token))

    return render_template("pay_card.html", payment=p, purchase=purchase, app_name=os.getenv("APP_NAME", "Sons & Sabores"))

@bp_purchase.get("/pay/return/<token>")
def pay_return(token: str):
    with db() as s:
        purchase = s.scalar(
            select(Purchase).where(Purchase.token == token)
        )
        if not purchase:
            abort(404)

        payment = s.scalar(
            select(Payment)
            .where(Payment.purchase_id == purchase.id)
            .order_by(Payment.id.desc())
        )

    return render_template(
        "payment_return.html",
        purchase=purchase,
        payment=payment,
        app_name=os.getenv("APP_NAME", "Sons & Sabores"),
    )
