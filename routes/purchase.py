# routes/purchase.py
import os
import secrets
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

from werkzeug.utils import secure_filename
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for, current_app
from sqlalchemy import select, desc, func

from db import db
from models import Event, Purchase, Payment, Show
from app_services.email_service import send_email
from app_services.email_templates import build_reservation_received_email

bp_purchase = Blueprint("purchase", __name__)

# ✅ Timezone São Paulo (para gravar no banco no horário local)
SAO_PAULO_TZ = ZoneInfo("America/Sao_Paulo")

def now_sp() -> datetime:
    """Datetime NAIVE no horário de São Paulo (bom para MySQL DateTime)."""
    return datetime.now(SAO_PAULO_TZ).replace(tzinfo=None)

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

def _status_label(st: str, *, admin: bool = False) -> str:
    st = (st or "").lower().strip()
    if admin:
        labels = {
            "reservation_pending": "Reserva registrada (aguardando confirmação)",
            "reservation_pending_price": "Reserva registrada (preço em definição)",
            "pending_payment": "Pagamento pendente (Pix)",
            "paid": "Reserva confirmada ✅ (pago)",
            "reserved": "Reserva confirmada ✅",
            "cancelled": "Cancelada ❌",
            "failed": "Falhou / expirada",
        }
    else:
        labels = {
            "reservation_pending": "Reserva registrada (aguardando confirmação)",
            "reservation_pending_price": "Reserva registrada (preço em definição)",
            "pending_payment": "Aguardando pagamento",
            "paid": "Reserva confirmada ✅",
            "reserved": "Reserva confirmada ✅",
            "cancelled": "Reserva cancelada",
            "failed": "Falhou / expirada",
        }
    return labels.get(st, st or "—")

def send_reservation_notification(purchase: Purchase) -> None:
    """E-mail interno: nova reserva."""
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

    for to_email in recipients:
        try:
            send_email(to_email=to_email, subject=subject, body_text=body)
        except Exception as e:
            current_app.logger.warning("[RESERVATION EMAIL] falhou para %s: %s", to_email, e)

# ---------------------------
# PÁGINA DE COMPRA/RESERVA
# ---------------------------
@bp_purchase.get("/buy/<event_slug>")
def buy(event_slug: str):
    fallback_price_cents = int(os.getenv("TICKET_PRICE_CENTS", "5000"))
    preselect_slug = (request.args.get("show_slug") or "").strip()

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

    show_prices_map = {sh.name: (sh.price_cents if sh.price_cents is not None else None) for sh in shows}
    show_requires_map = {sh.name: int(sh.requires_ticket or 0) for sh in shows}

    return render_template(
        "buy.html",
        event=ev,
        shows=shows,
        app_name=os.getenv("APP_NAME", "Sons & Sabores"),
        form={},
        ticket_price_cents=fallback_price_cents,
        show_prices_map=show_prices_map,
        show_requires_map=show_requires_map,
        preselect_slug=preselect_slug,
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

    purchase_token = secrets.token_urlsafe(24)

    with db() as s:
        ev = s.scalar(select(Event).where(Event.slug == event_slug))
        if not ev:
            abort(404)

        sh = s.scalar(select(Show).where(Show.name == show_name, Show.is_active == 1))
        if not sh:
            flash("Show inválido ou indisponível.", "error")
            return redirect(url_for("purchase.buy", event_slug=event_slug))

        total_people = 1 + len(guests_lines)

        # =========================================================
        # ✅ LOTAÇÃO (capacity) — corrigido (indent + filtro por event)
        # =========================================================
        cap = int(getattr(sh, "capacity", 0) or 0)
        if cap > 0:
            used = s.scalar(
                select(func.coalesce(func.sum(Purchase.ticket_qty), 0))
                .where(
                    Purchase.event_id == ev.id,
                    Purchase.show_name == show_name,
                    Purchase.status.in_([
                        "reservation_pending",
                        "reservation_pending_price",
                        "pending_payment",
                        "paid",
                        "reserved",
                    ])
                )
            ) or 0

            if used + total_people > cap:
                flash("Este show já atingiu a lotação. Selecione outra atração.", "error")
                return redirect(url_for("purchase.buy", event_slug=event_slug))

        # =========================================================
        # ✅ DEDUPE (evita clique duplo) — com horário SP
        # =========================================================
        dedupe_window_sec = int(os.getenv("RESERVATION_DEDUPE_SECONDS", "120"))
        cutoff = now_sp() - timedelta(seconds=dedupe_window_sec)

        existing = s.scalar(
            select(Purchase)
            .where(
                Purchase.event_id == ev.id,
                Purchase.show_name == show_name,
                Purchase.buyer_cpf_digits == cpf_digits,
                Purchase.created_at >= cutoff,
                Purchase.ticket_qty == total_people,
                (Purchase.guests_text == guests_text),
                Purchase.status.in_(["reservation_pending", "reservation_pending_price", "pending_payment"]),
            )
            .order_by(Purchase.id.desc())
        )
        if existing:
            current_app.logger.info("[DEDUPE] Reusando purchase recente token=%s", existing.token)
            flash("Já recebemos sua solicitação ✅ Abrindo o status.", "success")
            return redirect(url_for("purchase.purchase_status", token=existing.token))

        # =========================================================
        # CASO A — apenas reserva (sem ingresso)
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
                created_at=now_sp(),
                ticket_qty=total_people,
                ticket_unit_price_cents=0,
            )
            s.add(purchase)
            s.commit()

            send_reservation_notification(purchase)

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
                    unit_price_cents=purchase.ticket_unit_price_cents,  # ✅ (0)
                )
                try:
                    send_email(to_email=buyer_email, subject=subject, body_text=text, body_html=html)
                    purchase.reservation_received_email_sent_at = now_sp()
                    purchase.reservation_received_email_sent_to = buyer_email
                    purchase.reservation_received_email_last_error = None
                    s.add(purchase)
                    s.commit()
                except Exception as e:
                    purchase.reservation_received_email_last_error = str(e)[:2000]
                    s.add(purchase)
                    s.commit()

            flash("Reserva enviada ✅ Já já você recebe a confirmação por e-mail.", "success")
            return redirect(url_for("purchase.purchase_status", token=purchase.token))

        # =========================================================
        # CASO B — show exige ingresso, mas preço ainda não definido
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
                created_at=now_sp(),
                ticket_qty=total_people,
                ticket_unit_price_cents=0,
            )
            s.add(purchase)
            s.commit()

            send_reservation_notification(purchase)

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
                    price_pending=True,
                    guests=guests,
                    unit_price_cents=purchase.ticket_unit_price_cents,  # ✅ (0, não será exibido pq pending)
                )
                try:
                    send_email(to_email=buyer_email, subject=subject, body_text=text, body_html=html)
                    purchase.reservation_received_email_sent_at = now_sp()
                    purchase.reservation_received_email_sent_to = buyer_email
                    purchase.reservation_received_email_last_error = None
                    s.add(purchase)
                    s.commit()
                except Exception as e:
                    purchase.reservation_received_email_last_error = str(e)[:2000]
                    s.add(purchase)
                    s.commit()

            return redirect(url_for("purchase.purchase_status", token=purchase.token))

        # =========================================================
        # CASO C — pagamento (Pix manual)
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
            created_at=now_sp(),
            ticket_qty=total_people,
            ticket_unit_price_cents=unit_price_cents,  # ✅ preço do show congelado
        )
        s.add(purchase)
        s.commit()

        payment = Payment(
            purchase_id=purchase.id,
            provider="manual_pix",
            amount_cents=total_cents,
            currency="BRL",
            status="pending",
            external_id=None,
        )
        s.add(payment)
        s.commit()

        # ✅ avisar admin também quando está pendente de pagamento
        send_reservation_notification(purchase)

        return redirect(url_for("purchase.pay_manual", token=purchase.token))

# ---------------------------
# PÁGINA PIX MANUAL + UPLOAD
# ---------------------------
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

        payment = s.scalar(select(Payment).where(Payment.purchase_id == purchase.id).order_by(desc(Payment.id)))
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

@bp_purchase.post("/pay/manual/upload/<token>")
def upload_receipt(token: str):
    with db() as s:
        purchase = s.scalar(select(Purchase).where(Purchase.token == token))
        if not purchase:
            abort(404)
        payment = s.scalar(select(Payment).where(Payment.purchase_id == purchase.id).order_by(desc(Payment.id)))
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

# ---------------------------
# STATUS
# ---------------------------
@bp_purchase.get("/status/<token>")
def purchase_status(token: str):
    with db() as s:
        purchase = s.scalar(select(Purchase).where(Purchase.token == token))
        if not purchase:
            abort(404)
        payment = s.scalar(select(Payment).where(Payment.purchase_id == purchase.id).order_by(desc(Payment.id)))

    return render_template(
        "purchase_status.html",
        purchase=purchase,
        payment=payment,
        app_name=os.getenv("APP_NAME", "Sons & Sabores"),
    )

