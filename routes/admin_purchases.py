# routes/admin_purchases.py
from datetime import datetime

from flask import Blueprint, render_template, request, abort, flash, redirect, url_for
from sqlalchemy import select, desc, func

from db import db
from models import Purchase, Payment, Ticket
from routes.admin_auth import admin_required

from app_services.email_service import send_email
from app_services.email_templates import build_tickets_email


bp_admin_purchases = Blueprint("admin_purchases", __name__)


@bp_admin_purchases.get("/admin/purchases")
@admin_required
def admin_purchases_table():
    q = (request.args.get("q") or "").strip().lower()

    with db() as s:
        purchases = list(s.scalars(select(Purchase).order_by(desc(Purchase.id)).limit(400)))

        rows = []
        for p in purchases:
            pay_paid = s.scalar(
                select(Payment)
                .where(Payment.purchase_id == p.id, Payment.status == "paid")
                .order_by(desc(Payment.id))
            )
            pay = pay_paid or s.scalar(
                select(Payment)
                .where(Payment.purchase_id == p.id)
                .order_by(desc(Payment.id))
            )

            ticket_count = s.scalar(
                select(func.count()).select_from(Ticket).where(Ticket.purchase_id == p.id)
            ) or 0

            hay = " ".join([
                (p.buyer_name or ""),
                (p.buyer_cpf or ""),
                (p.show_name or ""),
                (p.token or ""),
                (pay.provider if pay else ""),
                (pay.status if pay else ""),
                (p.buyer_email or ""),
                (p.buyer_phone or ""),
            ]).lower()

            if q and q not in hay:
                continue

            rows.append({
                "purchase": p,
                "payment": pay,
                "ticket_count": ticket_count,
            })

    return render_template("admin_purchases_table.html", rows=rows, q=q)


@bp_admin_purchases.post("/admin/purchases/send-email/<int:purchase_id>")
@admin_required
def admin_send_purchase_email(purchase_id: int):
    to_email = (request.form.get("to_email") or "").strip()
    if not to_email or "@" not in to_email:
        flash("Informe um e-mail válido.", "error")
        return redirect(url_for("admin_purchases.admin_purchases_table"))

    # carrega purchase/payment/tickets
    with db() as s:
        purchase = s.get(Purchase, purchase_id)
        if not purchase:
            abort(404)

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

        tickets = list(
            s.scalars(
                select(Ticket).where(Ticket.purchase_id == purchase.id).order_by(Ticket.id.asc())
            )
        )

    pdf_all_url = (payment.tickets_pdf_url or "") if payment else ""
    zip_url = (payment.tickets_zip_url or "") if payment else ""

    ticket_rows = []
    for t in tickets:
        ticket_rows.append({
            "name": t.person_name,
            "pdf": t.pdf_path or "",
            "png": t.png_path or "",
        })

    subject, text, html = build_tickets_email(
        buyer_name=purchase.buyer_name or "Cliente",
        show_name=purchase.show_name or "Sons & Sabores",
        total_brl=((payment.amount_cents or 0) / 100) if payment else 0.0,
        token=purchase.token,
        ticket_qty=int(purchase.ticket_qty or len(tickets) or 1),                 # ✅
        unit_price_cents=int(purchase.ticket_unit_price_cents or 0),              # ✅
        pdf_all_url=pdf_all_url,
        zip_url=zip_url,
        tickets=ticket_rows,
    )


    try:
        send_email(
            to_email=to_email,
            subject=subject,
            body_text=text,
            body_html=html,
        )

        # marca como enviado no payment mais recente
        with db() as s:
            pay_db = s.scalar(
                select(Payment)
                .where(Payment.purchase_id == purchase.id)
                .order_by(desc(Payment.id))
            )
            if pay_db:
                pay_db.tickets_email_sent_at = datetime.utcnow()
                pay_db.tickets_email_sent_to = to_email
                pay_db.tickets_email_last_error = None
                s.add(pay_db)

        flash("E-mail enviado ✅", "success")

    except Exception as e:
        with db() as s:
            pay_db = s.scalar(
                select(Payment)
                .where(Payment.purchase_id == purchase.id)
                .order_by(desc(Payment.id))
            )
            if pay_db:
                pay_db.tickets_email_last_error = str(e)[:2000]
                s.add(pay_db)

        flash(f"Falha ao enviar e-mail: {e}", "error")

    return redirect(url_for("admin_purchases.admin_purchases_table"))

@bp_admin_purchases.post("/admin/purchases/send-email-buyer/<int:purchase_id>")
@admin_required
def admin_send_purchase_email_buyer(purchase_id: int):
    # Busca compra/pagamento/tickets
    with db() as s:
        purchase = s.get(Purchase, purchase_id)
        if not purchase:
            abort(404)

        to_email = (purchase.buyer_email or "").strip()
        if not to_email or "@" not in to_email:
            flash("Esta compra não tem e-mail válido do comprador.", "error")
            return redirect(url_for("admin_purchases.admin_purchases_table"))

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

        tickets = list(
            s.scalars(select(Ticket).where(Ticket.purchase_id == purchase.id).order_by(Ticket.id.asc()))
        )

    pdf_all_url = (payment.tickets_pdf_url or "") if payment else ""
    zip_url = (payment.tickets_zip_url or "") if payment else ""

    ticket_rows = [{"name": t.person_name, "pdf": t.pdf_path or "", "png": t.png_path or ""} for t in tickets]

    subject, text, html = build_tickets_email(
        buyer_name=purchase.buyer_name or "Cliente",
        show_name=purchase.show_name or "Sons & Sabores",
        total_brl=((payment.amount_cents or 0) / 100) if payment else 0.0,
        token=purchase.token,
        ticket_qty=int(purchase.ticket_qty or len(tickets) or 1),                 # ✅
        unit_price_cents=int(purchase.ticket_unit_price_cents or 0),              # ✅
        pdf_all_url=pdf_all_url,
        zip_url=zip_url,
        tickets=ticket_rows,
    )


    try:
        send_email(to_email=to_email, subject=subject, body_text=text, body_html=html)

        # marca status enviado
        with db() as s:
            pay_db = s.scalar(
                select(Payment)
                .where(Payment.purchase_id == purchase.id)
                .order_by(desc(Payment.id))
            )
            if pay_db:
                pay_db.tickets_email_sent_at = datetime.utcnow()
                pay_db.tickets_email_sent_to = to_email
                pay_db.tickets_email_last_error = None
                s.add(pay_db)

        flash("E-mail enviado para o comprador ✅", "success")

    except Exception as e:
        with db() as s:
            pay_db = s.scalar(
                select(Payment)
                .where(Payment.purchase_id == purchase.id)
                .order_by(desc(Payment.id))
            )
            if pay_db:
                pay_db.tickets_email_last_error = str(e)[:2000]
                s.add(pay_db)

        flash(f"Falha ao enviar e-mail: {e}", "error")

    return redirect(url_for("admin_purchases.admin_purchases_table"))
