# routes/purchase.py
import os
import secrets
from datetime import datetime, timedelta
from pathlib import Path

from werkzeug.utils import secure_filename

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for, current_app
from sqlalchemy import select, desc

from db import db
from models import Event, Purchase, Payment, Ticket, Show
from pagbank import create_pix_order  # Orders API (PIX)
from app_services.email_service import send_email
from app_services.email_templates import build_reservation_received_email

bp_purchase = Blueprint("purchase", __name__)

def _digits(s: str) -> str:
    return "".join(c for c in (s or "") if c.isdigit())


def _cpf_allowed(cpf_digits: str) -> bool:
    raw = (os.getenv("CPF_ALLOWLIST") or "").strip()
    if not raw:
        return True
    allow = {_digits(x) for x in raw.split(",") if _digits(x)}
    return cpf_digits in allow


def _parse_guests(guests_text: str) -> list[str]:
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


def _emails_from_env(var_name: str) -> list[str]:
    raw = (os.getenv(var_name) or "").strip()
    if not raw:
        return []
    parts = [p.strip() for p in raw.replace(";", ",").split(",")]
    return [p for p in parts if p]

def _status_label(st: str) -> str:
    st = (st or "").lower().strip()
    labels = {
        "reservation_pending": "Reserva registrada (aguardando confirmação)",
        "reservation_pending_price": "Reserva registrada (preço em definição)",
        "reserved": "Reserva confirmada ✅",
        "pending_payment": "Aguardando pagamento (Pix)",
        "paid": "Pagamento confirmado ✅",
        "failed": "Falhou / expirou",
        "cancelled": "Cancelado",
    }
    return labels.get(st, st or "—")


def send_reservation_notification(purchase: Purchase) -> None:
    """
    Dispara e-mail interno quando alguém faz RESERVA (sem pagamento).
    Destinatários vêm da ENV RESERVATION_NOTIFY_EMAILS (separado por vírgula).
    """
    recipients = _emails_from_env("RESERVATION_NOTIFY_EMAILS")
    if not recipients:
        return

    prefix = (os.getenv("RESERVATION_NOTIFY_SUBJECT_PREFIX") or "").strip()
    prefix = (prefix + " ") if prefix else ""

    subject = f"{prefix}Nova reserva · {purchase.show_name} · {purchase.buyer_name}"

    body = (
        "Nova reserva criada no site ✅\n\n"
        f"Show: {purchase.show_name}\n"
        f"Andamento: {_status_label(purchase.status)}\n"
        f"Comprador: {purchase.buyer_name}\n"
        f"CPF: {getattr(purchase, 'buyer_cpf', '')}\n"
        f"Email: {getattr(purchase, 'buyer_email', '')}\n"
        f"Telefone: {getattr(purchase, 'buyer_phone', '')}\n"
        f"Pessoas: {getattr(purchase, 'ticket_qty', 1)}\n"
        f"Token: {purchase.token}\n"
    )

    # best-effort: não quebra o fluxo de reserva se o SMTP falhar
    for to_email in recipients:
        try:
            send_email(
                to_email=to_email,
                subject=subject,
                body_text=body,
            )
        except Exception as e:
            current_app.logger.warning("[RESERVATION EMAIL] falhou para %s: %s", to_email, e)


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

    # price_cents pode ser NULL -> manda null pro JS (para bloquear compra)
    show_prices_map = {sh.name: (sh.price_cents if sh.price_cents is not None else None) for sh in shows}
    show_requires_map = {sh.name: int(sh.requires_ticket or 0) for sh in shows}

    return render_template(
        "buy.html",
        event=ev,
        shows=shows,
        app_name=os.getenv("APP_NAME", "Sons & Sabores"),
        form={},
        ticket_price_cents=fallback_price_cents,  # fallback
        show_prices_map=show_prices_map,
        show_requires_map=show_requires_map,
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

    if not _cpf_allowed(cpf_digits):
        flash("Este CPF ainda não está liberado para compra.", "error")
        return redirect(url_for("purchase.buy", event_slug=event_slug))

    guests_raw = (request.form.get("guests_text") or "").strip()
    guests_lines = _parse_guests(guests_raw)
    guests_text = "\n".join(guests_lines)

    exp_min = int(os.getenv("PIX_EXP_MINUTES", "30"))
    expires_at_local = datetime.utcnow() + timedelta(minutes=exp_min)

    purchase_token = secrets.token_urlsafe(24)

    with db() as s:
        ev = s.scalar(select(Event).where(Event.slug == event_slug))
        if not ev:
            abort(404)

        sh = s.scalar(
            select(Show).where(Show.name == show_name, Show.is_active == 1)
        )
        if not sh:
            flash("Show inválido ou indisponível.", "error")
            return redirect(url_for("purchase.buy", event_slug=event_slug))

        total_people = 1 + len(guests_lines)

        # =========================================================
        # CASO A — RESERVA SIMPLES (não exige ingresso)
        # =========================================================
        if int(sh.requires_ticket or 0) == 0:
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
                status="reservation_pending",
                created_at=datetime.utcnow(),
                ticket_qty=total_people,
                ticket_unit_price_cents=0,
            )
            s.add(purchase)
            s.commit()

            # 🔔 e-mail interno
            send_reservation_notification(purchase)

            # 📧 e-mail para o cliente — RESERVA REGISTRADA
            if buyer_email and "@" in buyer_email:
                guests = [g.strip() for g in guests_lines if g.strip()]
                status_url = f"{base_url}/status/{purchase.token}"

                subject, text, html = build_reservation_received_email(
                    buyer_name=buyer_name,
                    buyer_email=buyer_email,
                    show_name=show_name,
                    token=purchase.token,
                    ticket_qty=total_people,
                    status_url=status_url,
                    price_pending=False,
                    guests=guests,
                )
                try:
                    send_email(
                        to_email=buyer_email,
                        subject=subject,
                        body_text=text,
                        body_html=html,
                    )
                    current_app.logger.info(
                        "[RESERVATION RECEIVED EMAIL] sent token=%s to=%s",
                        purchase.token,
                        buyer_email,
                    )
                except Exception as e:
                    current_app.logger.warning(
                        "[RESERVATION RECEIVED EMAIL] failed token=%s err=%s",
                        purchase.token,
                        e,
                    )

            return redirect(url_for("purchase.purchase_status", token=purchase.token))

        # =========================================================
        # CASO B — RESERVA COM PREÇO PENDENTE
        # =========================================================
        if sh.price_cents is None:
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
                status="reservation_pending_price",
                created_at=datetime.utcnow(),
                ticket_qty=total_people,
                ticket_unit_price_cents=0,
            )
            s.add(purchase)
            s.commit()

            # ✅ EMAIL INTERNO
            send_reservation_notification(purchase)

            if buyer_email and "@" in buyer_email:
                guests = [g.strip() for g in (guests_lines or []) if g.strip()]

                base = (os.getenv("BASE_URL") or "").strip().rstrip("/")
                status_url = f"{base}/status/{purchase.token}" if base else ""

                subject, text, html = build_reservation_received_email(
                    buyer_name=buyer_name,
                    buyer_email=buyer_email,
                    show_name=show_name,
                    token=purchase.token,
                    ticket_qty=total_people,
                    status_url=status_url,
                    price_pending=(purchase.status == "reservation_pending_price"),
                    guests=guests,
                )

                try:
                    send_email(
                        to_email=buyer_email,
                        subject=subject,
                        body_text=text,
                        body_html=html,
                    )

                    # ✅ registra envio com sucesso
                    purchase.reservation_received_email_sent_at = datetime.utcnow()
                    purchase.reservation_received_email_sent_to = buyer_email
                    purchase.reservation_received_email_last_error = None
                    s.add(purchase)
                    s.commit()

                    current_app.logger.info(
                        "[RESERVATION RECEIVED EMAIL] sent token=%s to=%s",
                        purchase.token,
                        buyer_email,
                    )

                except Exception as e:
                    # ❌ registra erro
                    purchase.reservation_received_email_last_error = str(e)[:2000]
                    s.add(purchase)
                    s.commit()

                    current_app.logger.warning(
                        "[RESERVATION RECEIVED EMAIL] failed token=%s err=%s",
                        purchase.token,
                        e,
                    )

            return redirect(url_for("purchase.purchase_status", token=purchase.token))

        # =========================================================
        # CASO C — SHOW COM INGRESSO (fluxo de pagamento)
        # =========================================================
        unit_price_cents = int(sh.price_cents)
        total_cents = unit_price_cents * total_people

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

        # já pago -> vai para status (sem mostrar ingressos ao cliente)
        if (purchase.status or "").lower() == "paid" or (p.status or "").lower() == "paid":
            return redirect(url_for("purchase.purchase_status", token=purchase.token))

        # expirou -> marca failed
        if p.expires_at and p.status == "pending" and p.expires_at < now:
            p.status = "failed"
            purchase.status = "failed"
            s.add(p)
            s.add(purchase)
            s.commit()
            return redirect(url_for("purchase.pay_return", token=purchase.token))

        unit_price_cents = int(purchase.ticket_unit_price_cents or int(os.getenv("TICKET_PRICE_CENTS", "5000")))
        total_people = int(purchase.ticket_qty or 1)

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
    with db() as s:
        purchase = s.scalar(select(Purchase).where(Purchase.token == token))
        if not purchase:
            abort(404)
    return redirect(url_for("purchase.purchase_status", token=purchase.token))


@bp_purchase.get("/pay/manual/<token>")
def pay_manual(token: str):
    pix_key = (os.getenv("PIX_MANUAL_KEY") or "").strip()
    whatsapp_number = _digits(os.getenv("WHATSAPP_NUMBER", "")).strip()
    receiver = (os.getenv("PIX_MANUAL_RECEIVER_NAME") or "").strip()
    bank = (os.getenv("PIX_MANUAL_BANK") or "").strip()

    qr_image_url = url_for("static", filename="pix_qr.png") if os.path.exists("static/pix_qr.png") else ""

    with db() as s:
        purchase = s.scalar(select(Purchase).where(Purchase.token == token))
        if not purchase:
            abort(404)

        st = (purchase.status or "").lower()

        if st == "paid":
            return redirect(url_for("purchase.purchase_status", token=purchase.token))

        if st != "pending_payment":
            return redirect(url_for("purchase.purchase_status", token=purchase.token))

        payment = s.scalar(
            select(Payment)
            .where(Payment.purchase_id == purchase.id)
            .order_by(desc(Payment.id))
        )

        if not payment:
            return redirect(url_for("purchase.purchase_status", token=purchase.token))

    max_mb = int(os.getenv("RECEIPT_MAX_MB", "6"))
    unit_price_cents = int(purchase.ticket_unit_price_cents or int(os.getenv("TICKET_PRICE_CENTS", "5000")))
    unit_price_brl = unit_price_cents / 100
    ticket_qty = int(purchase.ticket_qty or 1)

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
            select(Payment).where(Payment.purchase_id == purchase.id).order_by(desc(Payment.id))
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

    total_brl = (payment.amount_cents or 0) / 100
    ticket_qty = int(purchase.ticket_qty or 1)
    unit_brl = (int(purchase.ticket_unit_price_cents or 0) / 100)

    subject = f"Comprovante PIX · {purchase.buyer_name} · {purchase.show_name}"
    body = (
        "Novo comprovante enviado pelo site.\n\n"
        f"Show: {purchase.show_name}\n"
        f"Comprador: {purchase.buyer_name}\n"
        f"CPF: {purchase.buyer_cpf}\n"
        f"Email: {purchase.buyer_email}\n"
        f"Telefone: {purchase.buyer_phone}\n"
        f"Token: {purchase.token}\n"
        f"Ingressos/Pessoas: {ticket_qty} × R$ {unit_brl:.2f}\n"
        f"Valor total: R$ {total_brl:.2f}\n\n"
        "Abra o painel Admin → Pendências/Compras para confirmar o pagamento.\n"
        "Os ingressos serão enviados em até 72 horas.\n"
    )

    file_bytes = tmp_path.read_bytes()
    attachments = [{
        "filename": safe,
        "content_type": mime or "application/octet-stream",
        "data": file_bytes,
    }]

    send_email(
        to_email=to_email,
        subject=subject,
        body_text=body,
        attachments=attachments,
    )

    try:
        tmp_path.unlink(missing_ok=True)
    except Exception:
        pass

    flash("Comprovante enviado ✅ Obrigado!", "success")
    return redirect(url_for("purchase.purchase_status", token=token))


@bp_purchase.get("/status/<token>")
def purchase_status(token: str):
    with db() as s:
        purchase = s.scalar(select(Purchase).where(Purchase.token == token))
        if not purchase:
            abort(404)

        payment = s.scalar(
            select(Payment).where(Payment.purchase_id == purchase.id).order_by(desc(Payment.id))
        )

    return render_template(
        "purchase_status.html",
        purchase=purchase,
        payment=payment,  # pode ser None (reserva)
        app_name=os.getenv("APP_NAME", "Sons & Sabores"),
    )
from app_services.email_templates import build_reservation_received_email

def _parse_guests_for_email(guests_text: str) -> list[str]:
    raw = (guests_text or "").strip()
    if not raw:
        return []
    names = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.replace(";", ",").split(",") if p.strip()]
        names.extend(parts)
    return names


def send_reservation_confirmation_to_buyer(purchase: Purchase) -> None:
    """
    Envia e-mail ao cliente confirmando que a RESERVA foi registrada,
    incluindo nomes dos acompanhantes.
    """
    buyer_email = (getattr(purchase, "buyer_email", "") or "").strip()
    if not buyer_email:
        return

    base_url = (os.getenv("BASE_URL") or "").strip().rstrip("/")
    status_url = f"{base_url}/status/{purchase.token}" if base_url else ""

    price_pending = ((purchase.status or "").lower() == "reservation_pending_price")

    guests = _parse_guests_for_email(getattr(purchase, "guests_text", ""))

    subject, text, html = build_reservation_received_email(
        buyer_name=purchase.buyer_name,
        buyer_email=buyer_email,
        show_name=purchase.show_name,
        token=purchase.token,
        ticket_qty=int(getattr(purchase, "ticket_qty", 1) or 1),
        status_url=status_url,
        price_pending=price_pending,
        guests=guests,  # ✅ NOVO
    )

    try:
        send_email(
            to_email=buyer_email,
            subject=subject,
            body_text=text,
            body_html=html,
        )
    except Exception as e:
        current_app.logger.warning(
            "[RESERVATION CONFIRM] falhou para %s: %s",
            buyer_email,
            e,
        )
