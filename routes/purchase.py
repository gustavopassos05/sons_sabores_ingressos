# routes/purchase.py
import os
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from werkzeug.utils import secure_filename

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for, current_app
from sqlalchemy import select, desc

from db import db
from models import Event, Purchase, Payment, Ticket
from pagbank import create_pix_order  # Orders API (PIX)
from app_services.email_service import send_email
from app_services.ticket_generator import slug_filename
from models import AdminSetting
from models import Show

bp_purchase = Blueprint("purchase", __name__)

def _get_show_price_cents(s, show_name: str) -> int:
    # fallback geral
    fallback = int(os.getenv("TICKET_PRICE_CENTS", "5000"))

    k = f"PRICE_{slug_filename(show_name)}"
    row = s.scalar(select(AdminSetting).where(AdminSetting.key == k))
    try:
        v = int((row.value or "").strip()) if row else fallback
        return v if v > 0 else fallback
    except Exception:
        return fallback

def _digits(s: str) -> str:
    return "".join(c for c in (s or "") if c.isdigit())


def _cpf_allowed(cpf_digits: str) -> bool:
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


def _parse_guests(guests_text: str) -> list[str]:
    """
    Aceita convidados por linha ou separado por ',' ';'
    """
    raw = (guests_text or "").strip()
    if not raw:
        return []
    tmp: list[str] = []
    for line in raw.splitlines():
        line = (line or "").strip()
        if not line:
            continue
        parts = [p.strip() for p in line.replace(";", ",").split(",") if p.strip()]
        tmp.extend(parts)
    return tmp

@bp_purchase.get("/buy/<event_slug>")
def buy(event_slug: str):
    fallback_price_cents = int(os.getenv("TICKET_PRICE_CENTS", "5000"))

    with db() as s:
        ev = s.scalar(select(Event).where(Event.slug == event_slug))
        if not ev:
            abort(404)

        shows = list(
            s.scalars(
                select(Show)
                .where(Show.is_active == 1)
                .order_by(desc(Show.id))
            )
        )

    # mapa de preços por nome do show (se price_cents for NULL, usa fallback)
    show_prices_map = {sh.name: (sh.price_cents or fallback_price_cents) for sh in shows}

    return render_template(
        "buy.html",
        event=ev,
        shows=shows,  # ✅ lista de shows com name/date_text/price_cents
        app_name=os.getenv("APP_NAME", "Sons & Sabores"),
        form={},
        ticket_price_cents=fallback_price_cents,  # fallback
        show_prices_map=show_prices_map,
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

    # ✅ trava allowlist (soft launch)
    if not _cpf_allowed(cpf_digits):
        flash("Este CPF ainda não está liberado para compra.", "error")
        return redirect(url_for("purchase.buy", event_slug=event_slug))

    guests_raw = (request.form.get("guests_text") or "").strip()
    guests_lines = _parse_guests(guests_raw)
    guests_text = "\n".join(guests_lines)

    # preço por show vindo do banco (AdminSetting)
    unit_price_cents = _get_show_price_cents(s, show_name)
    total_people = 1 + len(guests_lines)
    total_cents = unit_price_cents * total_people

    exp_min = int(os.getenv("PIX_EXP_MINUTES", "30"))
    expires_at_local = datetime.utcnow() + timedelta(minutes=exp_min)

    purchase_token = secrets.token_urlsafe(24)

    with db() as s:
        ev = s.scalar(select(Event).where(Event.slug == event_slug))
        if not ev:
            abort(404)

        # ✅ SEM anti-duplicidade: sempre cria nova compra, mesmo CPF

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
            ticket_qty=total_people,
            ticket_unit_price_cents=unit_price_cents,

        )
        s.add(purchase)
        s.commit()

        pagbank_notification_url = f"{base_url}/webhooks/pagbank"

        # tenta PIX Orders API -> se der whitelist, cai no manual
        try:
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

        except Exception as e:
            current_app.logger.warning("[PIX FALLBACK] create_pix_order falhou: %s", e)

            payment = Payment(
                purchase_id=purchase.id,
                provider="manual_pix",
                amount_cents=total_cents,
                currency="BRL",
                status="pending",
                external_id=None,
                expires_at=expires_at_local,
                paid_at=None,
            )
            s.add(payment)
            s.commit()

            return redirect(url_for("purchase.pay_manual", token=purchase.token))

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

        if (purchase.status or "").lower() == "paid" or (p.status or "").lower() == "paid":
            return redirect(url_for("tickets.purchase_public", token=purchase.token))

        if p.expires_at and p.status == "pending" and p.expires_at < now:
            p.status = "failed"
            purchase.status = "failed"
            s.add(p)
            s.add(purchase)
            s.commit()
            return redirect(url_for("purchase.pay_return", token=purchase.token))

        unit_price_cents = int(os.getenv("TICKET_PRICE_CENTS", "5000"))
        guests_lines = _parse_guests(purchase.guests_text or "")
        total_people = 1 + len(guests_lines)

    return render_template(
        "pay_pix.html",
        app_name=os.getenv("APP_NAME", "Sons & Sabores"),
        payment=p,
        purchase=purchase,
        event=ev,
        ticket_price_cents=unit_price_cents,
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

    if (purchase.status or "").lower() == "paid":
        return redirect(url_for("tickets.purchase_public", token=purchase.token))

    return render_template(
        "payment_return.html",
        purchase=purchase,
        payment=payment,
        tickets=tickets,
        app_name=os.getenv("APP_NAME", "Sons & Sabores"),
    )


@bp_purchase.get("/pay/manual/<token>")
def pay_manual(token: str):
    # mantém ENV por enquanto (você já tem WHATSAPP_NUMBER no Render)
    pix_key = (os.getenv("PIX_MANUAL_KEY") or "").strip()
    whatsapp_number = _digits(os.getenv("WHATSAPP_NUMBER", "")).strip()

    receiver = (os.getenv("PIX_MANUAL_RECEIVER_NAME") or "").strip()
    bank = (os.getenv("PIX_MANUAL_BANK") or "").strip()

    qr_image_url = url_for("static", filename="pix_qr.png") if os.path.exists("static/pix_qr.png") else ""

    with db() as s:
        purchase = s.scalar(select(Purchase).where(Purchase.token == token))
        if not purchase:
            abort(404)

        payment_paid = s.scalar(
            select(Payment)
            .where(Payment.purchase_id == purchase.id, Payment.status == "paid")
            .order_by(Payment.id.desc())
        )
        if payment_paid:
            return redirect(url_for("tickets.purchase_public", token=purchase.token))

        payment = s.scalar(
            select(Payment).where(Payment.purchase_id == purchase.id).order_by(Payment.id.desc())
        )
        if not payment:
            abort(404)

    max_mb = int(os.getenv("RECEIPT_MAX_MB", "6"))
    unit_price_cents = int(os.getenv("TICKET_PRICE_CENTS", "5000"))
    unit_price_brl = unit_price_cents / 100
    ticket_qty = (payment.amount_cents or 0) // unit_price_cents if unit_price_cents else 0


    return render_template(
        "pay_manual.html",
        purchase=purchase,
        payment=payment,
        pix_key=pix_key,
        qr_image_url=qr_image_url,
        whatsapp_number=whatsapp_number,
        receiver=receiver,
        bank=bank,
        max_mb=max_mb,
        app_name=os.getenv("APP_NAME", "Sons & Sabores"),
        unit_price_cents=unit_price_cents,
        unit_price_brl=unit_price_brl,
        ticket_qty=ticket_qty,

    )


@bp_purchase.get("/pay/manual/thanks/<token>")
def pay_manual_thanks(token: str):
    with db() as s:
        purchase = s.scalar(select(Purchase).where(Purchase.token == token))
        if not purchase:
            abort(404)
    return render_template(
        "pay_manual_thanks.html",
        purchase=purchase,
        app_name=os.getenv("APP_NAME", "Sons & Sabores"),
    )


@bp_purchase.post("/pay/manual/upload/<token>")
def upload_receipt(token: str):
    with db() as s:
        purchase = s.scalar(select(Purchase).where(Purchase.token == token))
        if not purchase:
            abort(404)

        payment = s.scalar(
            select(Payment).where(Payment.purchase_id == purchase.id).order_by(Payment.id.desc())
        )
        if not payment:
            abort(404)

    f = request.files.get("receipt_file")
    if not f or not f.filename:
        flash("Selecione um arquivo (imagem ou PDF).", "error")
        return redirect(url_for("purchase.pay_manual", token=token))

    max_mb = int(os.getenv("RECEIPT_MAX_MB", "6"))
    max_bytes = max_mb * 1024 * 1024

    f.stream.seek(0, os.SEEK_END)
    size = f.stream.tell()
    f.stream.seek(0)

    if size > max_bytes:
        flash(f"Arquivo muito grande (máx {max_mb}MB).", "error")
        return redirect(url_for("purchase.pay_manual", token=token))

    mime = (f.mimetype or "").lower()
    allowed = {"application/pdf", "image/png", "image/jpeg", "image/jpg", "image/webp"}
    if mime not in allowed and not mime.startswith("image/"):
        flash("Formato inválido. Envie imagem ou PDF.", "error")
        return redirect(url_for("purchase.pay_manual", token=token))

    tmp_dir = Path("/tmp/receipts").resolve()
    tmp_dir.mkdir(parents=True, exist_ok=True)

    safe = secure_filename(f.filename) or "comprovante"
    tmp_path = tmp_dir / f"{token}-{safe}"
    f.save(tmp_path)

    to_email = (os.getenv("RECEIPT_TO_EMAIL") or "").strip()
    if not to_email:
        raise RuntimeError("RECEIPT_TO_EMAIL não configurado no Render.")

    subject = f"Comprovante PIX · {purchase.buyer_name} · {purchase.show_name}"
    total_brl = (payment.amount_cents or 0) / 100

    body = (
        "Novo comprovante enviado pelo site.\n\n"
        f"Show: {purchase.show_name}\n"
        f"Comprador: {purchase.buyer_name}\n"
        f"CPF: {purchase.buyer_cpf}\n"
        f"Email: {purchase.buyer_email}\n"
        f"Telefone: {purchase.buyer_phone}\n"
        f"Token: {purchase.token}\n"
        f"Ingressos: {(payment.amount_cents or 0) // int(os.getenv('TICKET_PRICE_CENTS','5000'))} (R$ 50,00 cada)\n"
        f"Valor total: R$ {total_brl:.2f}\n\n"
        "Abra o painel Admin → Pendências/Compras para confirmar o pagamento.\n"
        "Os ingressos serão enviados em até 72 horas.\n"
    )

    # envia email simples (texto)
    send_email(
        to_email=to_email,
        subject=subject,
        body_text=body,
    )

    flash("Comprovante enviado ✅ Obrigado!", "success")
    return redirect(url_for("purchase.purchase_status", token=token))

@bp_purchase.get("/status/<token>")
def purchase_status(token: str):
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

    # SEM ingressos aqui
    return render_template(
        "purchase_status.html",
        purchase=purchase,
        payment=payment,
        app_name=os.getenv("APP_NAME", "Sons & Sabores"),
    )

def _get_show_price_cents(s, show_name: str) -> int:
    fallback = int(os.getenv("TICKET_PRICE_CENTS", "5000"))
    k = f"PRICE_{slug_filename(show_name)}"
    row = s.scalar(select(AdminSetting).where(AdminSetting.key == k))
    try:
        v = int((row.value or "").strip()) if row else fallback
        return v if v > 0 else fallback
    except Exception:
        return fallback
