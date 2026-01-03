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
            "BASE_URL não configurado nas env vars do Render (ex: https://borogodo-sons-sabores-ingressos.onrender.com)"
        )

    # --- método de pagamento vindo do form ---
    # valores sugeridos: "pix" (PagBank QR na tela) | "pagseguro" (Redirect PagSeguro: Pix ou Cartão)
    pay_method = (request.form.get("pay_method") or "pix").strip().lower()
    if pay_method not in ("pix", "pagseguro"):
        pay_method = "pix"

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

    qty_adult = int(request.form.get("qty_adult") or 1)

    guests_raw = (request.form.get("guests_text") or "").strip()
    guests_lines = [x.strip() for x in guests_raw.splitlines() if x.strip()]
    guests_text = "\n".join(guests_lines)

    price_cents_unit = int(os.getenv("TICKET_PRICE_CENTS", "5000"))
    total_people = 1 + len(guests_lines)  # comprador + acompanhantes
    total_cents = price_cents_unit * total_people
    total_brl = total_cents / 100.0

    exp_min = int(os.getenv("PIX_EXP_MINUTES", "30"))

    purchase_token = secrets.token_urlsafe(24)
    buyer_tax_id_digits = "".join(c for c in buyer_cpf if c.isdigit())
    buyer_phone_digits = "".join(c for c in buyer_phone if c.isdigit())

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
            qty_adult=qty_adult,
            status="pending_payment",
            created_at=datetime.utcnow(),
        )
        s.add(purchase)
        s.commit()

        # URLs de retorno/notificação
        # (use seu blueprint webhooks conforme você criou)
        pagbank_notification_url = f"{base_url}/webhooks/pagbank"
        pagseguro_notification_url = f"{base_url}/webhooks/pagseguro"   # você vai criar essa rota depois
        success_redirect_url = f"{base_url}/tickets/purchase/{purchase.token}"  # público após pago (ajuste se sua rota for diferente)

        item_name = f"Sons & Sabores - {show_name} ({total_people} ingresso(s))"

        # 2) decide provedor
        if pay_method == "pagseguro":
            # --- PagSeguro Redirect (checkout hospedado: Pix ou Cartão) ---
            from app_services.payments.pagseguro import create_checkout_redirect

            checkout_code, checkout_url = create_checkout_redirect(
                reference=f"purchase-{purchase.id}",
                item_description=item_name,
                amount_brl=total_brl,
                buyer_name=buyer_name,
                buyer_email=buyer_email,
                buyer_phone=buyer_phone_digits,
                buyer_cpf=buyer_tax_id_digits,
                redirect_url=success_redirect_url,
                notification_url=pagseguro_notification_url,
            )

            payment = Payment(
                purchase_id=purchase.id,
                provider="pagseguro",
                amount_cents=total_cents,
                currency="BRL",
                status="pending",
                external_id=checkout_code,  # guarda o "code" do PagSeguro
                qr_text=None,
                qr_image_base64=None,
                expires_at=None,
                paid_at=None,
            )
            s.add(payment)
            s.commit()

            # manda o usuário para o checkout (Pix ou Cartão) do PagSeguro
            return redirect(checkout_url)

        # --- PagBank Pix (QR code na sua tela) ---
        order_id, qr_text, qr_b64, expires_at = create_pix_order(
            reference_id=f"purchase-{purchase.id}",
            buyer_name=buyer_name,
            buyer_email=buyer_email,
            buyer_tax_id=buyer_tax_id_digits,
            buyer_phone_digits=buyer_phone_digits,
            item_name=item_name,
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
            expires_at=expires_at.replace(tzinfo=None) if expires_at else None,
            paid_at=None,
        )
        s.add(payment)
        s.commit()

        return redirect(url_for("purchase.pay_pix", payment_id=payment.id))

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
