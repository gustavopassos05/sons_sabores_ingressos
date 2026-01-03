import os
import secrets
from datetime import datetime

from db import db
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from sqlalchemy import select

from models import Event, Purchase, Payment
from pagbank import create_pix_order

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
        raise RuntimeError(
            "BASE_URL não configurado nas env vars do Render "
            "(ex: https://borogodo-sons-sabores-ingressos.onrender.com)"
        )

    show_name = (request.form.get("show_name") or "").strip()
    if not show_name:
        flash("Selecione o show.", "error")
        return redirect(url_for("purchase.buy", event_slug=event_slug))

    buyer_name = (request.form.get("buyer_name") or "").strip()
    if not buyer_name:
        flash("Nome do comprador é obrigatório.", "error")
        return redirect(url_for("purchase.buy", event_slug=event_slug))

    buyer_cpf = (request.form.get("buyer_cpf") or "").strip()
    buyer_email = (request.form.get("buyer_email") or "").strip()
    buyer_phone = (request.form.get("buyer_phone") or "").strip()

    guests_raw = (request.form.get("guests_text") or "").strip()
    guests_lines = [x.strip() for x in guests_raw.splitlines() if x.strip()]
    guests_text = "\n".join(guests_lines)

    price_cents_unit = int(os.getenv("TICKET_PRICE_CENTS", "5000"))
    total_people = 1 + len(guests_lines)  # comprador + acompanhantes
    total_cents = price_cents_unit * total_people
    amount_brl = total_cents / 100.0

    purchase_token = secrets.token_urlsafe(24)

    with db() as s:
        ev = s.scalar(select(Event).where(Event.slug == event_slug))
        if not ev:
            abort(404)

        # 1) cria purchase
        purchase = Purchase(
            event_id=ev.id,
            token=purchase_token,
            show_name=show_name,
            buyer_name=buyer_name,
            buyer_cpf=buyer_cpf,
            buyer_email=buyer_email,
            buyer_phone=buyer_phone,
            guests_text=guests_text,
            qty_adult=1,
            status="pending_payment",
            created_at=datetime.utcnow(),
        )
        s.add(purchase)
        s.commit()  # precisa do purchase.id

        # 2) cria checkout PagSeguro e REDIRECIONA
        notification_url = f"{base_url}/webhooks/pagseguro"
        redirect_url = f"{base_url}/purchase/{purchase.token}"  # página pública pós-pagamento

        checkout_code, pagseguro_url = create_checkout_redirect(
            reference=f"purchase-{purchase.id}",
            item_description=f"Sons & Sabores - {show_name} ({total_people} ingresso(s))",
            amount_brl=amount_brl,
            buyer_name=buyer_name,
            buyer_email=buyer_email,
            buyer_phone=buyer_phone,
            buyer_cpf=buyer_cpf,
            redirect_url=redirect_url,
            notification_url=notification_url,
        )

        # 3) registra payment no seu banco
        payment = Payment(
            purchase_id=purchase.id,
            provider="pagseguro",
            amount_cents=total_cents,
            currency="BRL",
            status="pending",
            external_id=checkout_code,  # code do PagSeguro
            qr_text=None,
            qr_image_base64=None,
            expires_at=None,
            paid_at=None,
        )
        s.add(payment)
        s.commit()

        # 4) manda o cliente pro checkout PagSeguro (Pix/Cartão)
        return redirect(pagseguro_url)

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
