# routes/purchase.py
import os
import secrets
from datetime import datetime

from db import db
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from sqlalchemy import select

from models import Event, Purchase, Payment
from pagbank import create_pix_order
from app_services.payments.pagbank_checkout import create_checkout_redirect

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
        form={},  # <-- aqui
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

    guests_raw = (request.form.get("guests_text") or "").strip()
    guests_lines = [x.strip() for x in guests_raw.splitlines() if x.strip()]
    guests_text = "\n".join(guests_lines)

    price_cents_unit = int(os.getenv("TICKET_PRICE_CENTS", "5000"))
    total_people = 1 + len(guests_lines)
    total_cents = price_cents_unit * total_people
    total_brl = total_cents / 100

    purchase_token = secrets.token_urlsafe(24)

    # Webhook do checkout (serve pra PIX e cartão dentro do PagBank Checkout)
    checkout_notification_url = f"{base_url}/webhooks/pagbank-checkout"

    # Para onde o PagBank manda o usuário de volta
    redirect_url = f"{base_url}/pay/return/{purchase_token}"

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

        checkout_id, checkout_url = create_checkout_redirect(
            reference=f"purchase-{purchase.id}",
            item_description=f"Sons & Sabores - {show_name} ({total_people} ingresso(s))",
            amount_brl=total_brl,
            buyer_name=buyer_name,
            buyer_email=buyer_email,
            buyer_phone=buyer_phone,
            buyer_cpf=buyer_cpf,
            redirect_url=redirect_url,
            payment_notification_url=checkout_notification_url,  # <- paga aqui
            checkout_notification_url=None,
        )

        # ✅ marca qualquer payment anterior como failed (garante que o "último" não confunde)
        s.execute(
            Payment.__table__.update()
            .where(Payment.purchase_id == purchase.id, Payment.status == "pending")
            .values(status="failed")
        )

        payment = Payment(
            purchase_id=purchase.id,
            provider="pagbank_checkout",
            amount_cents=total_cents,
            currency="BRL",
            status="pending",
            external_id=checkout_id,
            paid_at=None,
        )
        s.add(payment)
        s.commit()

    # ✅ aqui é o pulo do gato: vai DIRETO pro PagBank
    return redirect(checkout_url)

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

        # ✅ se já foi pago, manda direto pra página pública da compra (ingressos)
        if (purchase.status or "").lower() == "paid" or (p.status or "").lower() == "paid":
            return redirect(url_for("tickets.purchase_public", token=purchase.token))

        # ✅ marca expirado/failed automaticamente quando passar do expires_at
        # (assim a regra "expirada/failed -> deixa criar nova" funciona)
        if p.expires_at:
            exp = p.expires_at
            # protege caso venha com tzinfo por algum motivo
            if getattr(exp, "tzinfo", None) is not None:
                exp = exp.replace(tzinfo=None)

            if now > exp:
                p.status = "failed"  # ou "expired" se você preferir (aí ajusta o resto do sistema)
                s.add(p)

                # se a compra ainda estava pendente, marca como failed também
                if (purchase.status or "").lower() in ("pending_payment", "pending"):
                    purchase.status = "Expirado"
                    s.add(purchase)

                s.commit()

                # manda pra tela de retorno (onde você pode mostrar "expirado, gere outro")
                return redirect(url_for("purchase.pay_return", token=purchase.token))

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
    finalize_fn = current_app.extensions.get("finalize_purchase")

    with db() as s:
        purchase = s.scalar(select(Purchase).where(Purchase.token == token))
        if not purchase:
            abort(404)

        # ✅ 1) payment pago (se existir) — evita cair no pending errado
        payment_paid = s.scalar(
            select(Payment)
            .where(Payment.purchase_id == purchase.id, Payment.status == "paid")
            .order_by(Payment.id.desc())
        )

        # ✅ 2) senão, pega o último (pending/failed/expired)
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

        # ✅ se já está pago, mas ainda não gerou links, tenta finalizar (idempotente)
        should_finalize = (
            payment
            and (payment.status or "").lower() == "paid"
            and not payment.tickets_pdf_url
            and callable(finalize_fn)
        )

    # ⚠️ chama fora do "with db()" pra não misturar sessão
    if should_finalize:
        try:
            finalize_fn(purchase.id)
        except Exception:
            pass

        # recarrega depois do finalize
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
        tickets=tickets,          # ✅ AGORA TEM
        app_name=os.getenv("APP_NAME", "Sons & Sabores"),
    )
