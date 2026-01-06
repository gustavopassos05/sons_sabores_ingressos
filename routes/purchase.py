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

    pay_method = (request.form.get("pay_method") or "pix").lower().strip()

    show_name = (request.form.get("show_name") or "").strip()
    buyer_name = (request.form.get("buyer_name") or "").strip()
    buyer_cpf_raw = (request.form.get("buyer_cpf") or "").strip()
    buyer_email = (request.form.get("buyer_email") or "").strip()
    buyer_phone = (request.form.get("buyer_phone") or "").strip()

    if not show_name:
        flash("Selecione o show.", "error")
        return redirect(url_for("purchase.buy", event_slug=event_slug))

    if not buyer_name:
        flash("Nome do comprador é obrigatório.", "error")
        return redirect(url_for("purchase.buy", event_slug=event_slug))

    # ✅ normaliza CPF (só dígitos) e salva isso no banco
    buyer_cpf_digits = "".join(c for c in (buyer_cpf_raw or "") if c.isdigit())

    # (opcional) validação simples
    if buyer_cpf_digits and len(buyer_cpf_digits) != 11:
        flash("CPF inválido (precisa ter 11 dígitos).", "error")
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

    with db() as s:
        ev = s.scalar(select(Event).where(Event.slug == event_slug))
        if not ev:
            abort(404)

        # =========================================================
        # ✅ REGRA ANTI-DUPLICIDADE (CPF + show + evento)
        #
        # se tiver compra pendente -> manda pro pagamento existente
        # se tiver compra paga -> manda pra página de ingressos
        # se tiver compra expirada/failed -> deixa criar nova
        # =========================================================
        if buyer_cpf_digits:
            existing_purchase = s.scalar(
                select(Purchase)
                .where(
                    Purchase.event_id == ev.id,
                    Purchase.show_name == show_name,
                    Purchase.buyer_cpf == buyer_cpf_digits,  # CPF normalizado salvo aqui
                )
                .order_by(Purchase.id.desc())
            )

            if existing_purchase:
                existing_payment = s.scalar(
                    select(Payment)
                    .where(Payment.purchase_id == existing_purchase.id)
                    .order_by(Payment.id.desc())
                )

                p_status = (existing_purchase.status or "").lower()
                pay_status = ((existing_payment.status if existing_payment else "") or "").lower()

                # ✅ se pago -> ingressos
                if p_status == "paid" or pay_status == "paid":
                    return redirect(url_for("tickets.purchase_public", token=existing_purchase.token))

                # ✅ se pendente -> volta pro pagamento que já existe
                if p_status in ("pending_payment", "pending") or pay_status == "pending":
                    # PIX
                    if existing_payment and existing_payment.provider == "pagbank":
                        return redirect(url_for("purchase.pay_pix", payment_id=existing_payment.id))
                    # cartão/checkout (ou qualquer outro)
                    return redirect(url_for("purchase.pay_return", token=existing_purchase.token))

                # ✅ se failed/expired/cancelled -> deixa seguir e criar uma nova
                # (não faz nada)

        # -----------------------------
        # cria nova compra
        # -----------------------------
        purchase = Purchase(
            event_id=ev.id,
            token=purchase_token,
            show_name=show_name,
            buyer_name=buyer_name,
            buyer_cpf=buyer_cpf_digits or None,  # ✅ SALVA NORMALIZADO
            buyer_email=buyer_email,
            buyer_phone=buyer_phone,
            guests_text=guests_text,
            status="pending_payment",
            created_at=datetime.utcnow(),
        )
        s.add(purchase)
        s.commit()

        # ✅ Webhook do PagBank (PIX API nova)
        pagbank_notification_url = f"{base_url}/webhooks/pagbank"

        # ✅ Webhook do Checkout Redirect (cartão)
        checkout_notification_url = f"{base_url}/webhooks/pagbank-checkout"
        redirect_url = f"{base_url}/pay/return/{purchase.token}"

        # -----------------------------
        # CARTÃO (Checkout Redirect v2)
        # -----------------------------
        if pay_method == "card":
            # ✅ mata pendências antigas desse purchase (evita /return cair no payment errado)
            s.execute(
                Payment.__table__.update()
                .where(Payment.purchase_id == purchase.id, Payment.status == "pending")
                .values(status="failed")
            )

            checkout_id, checkout_url = create_checkout_redirect(
                reference=f"purchase-{purchase.id}",
                item_description=f"Sons & Sabores - {show_name} ({total_people} ingresso(s))",
                amount_brl=total_brl,
                buyer_name=buyer_name,
                buyer_email=buyer_email,
                buyer_phone=buyer_phone,
                buyer_cpf=buyer_cpf_digits or "",  # ✅ usa dígitos
                redirect_url=redirect_url,
                payment_notification_url=checkout_notification_url,  # <- PAID etc.
                checkout_notification_url=None,  # opcional (EXPIRED)
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

            return redirect(checkout_url)

        # -----------------------------
        # PIX (PagBank Orders API)
        # -----------------------------
        order_id, qr_text, qr_b64, expires_at = create_pix_order(
            reference_id=f"purchase-{purchase.id}",
            buyer_name=buyer_name,
            buyer_email=buyer_email,
            buyer_tax_id=buyer_cpf_digits,  # ✅ CPF em dígitos
            buyer_phone_digits=buyer_phone,  # (se quiser, dá pra normalizar também)
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
            expires_at=expires_at.replace(tzinfo=None) if expires_at else None,
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
    with db() as s:
        purchase = s.scalar(select(Purchase).where(Purchase.token == token))
        if not purchase:
            abort(404)

        # ✅ pega o "paid" mais recente, se existir
        paid_payment = s.scalar(
            select(Payment)
            .where(Payment.purchase_id == purchase.id, Payment.status == "paid")
            .order_by(Payment.id.desc())
        )

        if paid_payment:
            return redirect(url_for("tickets.purchase_public", token=purchase.token))

        # senão, mostra o último (pending/failed)
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
