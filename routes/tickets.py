# routes/tickets.py
from flask import Blueprint, render_template, abort
from sqlalchemy import select
import os
from db import db
from models import Purchase, Ticket, Payment
from app_services.email_service import send_email

bp_tickets = Blueprint("tickets", __name__)

@bp_tickets.get("/admin/tickets-old")
def admin_tickets_old():
    # você pode até apagar o template antigo depois
    return redirect(url_for("admin_panel.home"))

from flask import current_app

@bp_tickets.get("/purchase/<token>")
def purchase_public(token: str):
    finalize_fn = current_app.extensions.get("finalize_purchase")

    with db() as s:
        purchase = s.scalar(select(Purchase).where(Purchase.token == token))
        if not purchase:
            abort(404)

        # ✅ 1) payment pago (se existir)
        payment_paid = s.scalar(
            select(Payment)
            .where(Payment.purchase_id == purchase.id, Payment.status == "paid")
            .order_by(Payment.id.desc())
        )

        # ✅ 2) senão, pega o último (pending/failed)
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
            # não quebra a página; só deixa "gerando..."
            pass

        # recarrega dados após tentar finalizar
        with db() as s:
            purchase = s.scalar(select(Purchase).where(Purchase.token == token))
            tickets = list(s.scalars(select(Ticket).where(Ticket.purchase_id == purchase.id).order_by(Ticket.id.asc())))
            payment = s.scalar(select(Payment).where(Payment.purchase_id == purchase.id, Payment.status == "paid").order_by(Payment.id.desc())) \
                      or s.scalar(select(Payment).where(Payment.purchase_id == purchase.id).order_by(Payment.id.desc()))

    return redirect(url_for("purchase.purchase_status", token=token))

@bp_tickets.get("/ticket/<token>")
def ticket_public(token: str):
    with db() as s:
        t = s.scalar(select(Ticket).where(Ticket.token == token))
        if not t:
            abort(404)

        purchase = s.get(Purchase, t.purchase_id) if t.purchase_id else None
        if not purchase:
            abort(404)

        # pega payment PAID primeiro (evita pegar pending errado)
        payment_paid = s.scalar(
            select(Payment)
            .where(Payment.purchase_id == purchase.id, Payment.status == "paid")
            .order_by(desc(Payment.id))
        )
        payment = payment_paid or s.scalar(
            select(Payment)
            .where(Payment.purchase_id == purchase.id)
            .order_by(desc(Payment.id))
        )

    # Só valida como "válido" se pagamento confirmado e ticket emitido
    is_paid = (purchase.status or "").lower() == "paid" and payment and (payment.status or "").lower() == "paid"
    is_valid = is_paid and (t.status or "").lower() == "issued"

    return render_template(
        "ticket_public.html",
        ticket=t,
        purchase=purchase,
        is_valid=is_valid,
        app_name=os.getenv("APP_NAME", "Sons & Sabores"),
    )
