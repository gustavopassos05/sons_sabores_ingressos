# routes/admin_purchases.py
from flask import Blueprint, render_template, request, abort, current_app
from sqlalchemy import select, desc, func
from datetime import datetime

from db import db
from models import Purchase, Payment, Ticket
from routes.admin_auth import admin_required

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

    # Monta conteúdo do email (links)
    subject = f"Ingressos Sons & Sabores — {purchase.show_name}"
    total = (payment.amount_cents or 0) / 100 if payment else 0

    body = (
        f"Olá!\n\n"
        f"Segue o link para baixar seus ingressos do Sons & Sabores.\n\n"
        f"Show: {purchase.show_name}\n"
        f"Comprador: {purchase.buyer_name}\n"
        f"Total: R$ {total:.2f}\n"
        f"Token da compra: {purchase.token}\n\n"
        f"✅ Enviaremos em até 72 horas (caso ainda esteja em processamento).\n\n"
    )

    if payment and payment.tickets_zip_url:
        body += f"Baixar todos (ZIP): {payment.tickets_zip_url}\n"
    if payment and payment.tickets_pdf_url:
        body += f"PDF com todos: {payment.tickets_pdf_url}\n"

    # links individuais (opcional no email)
    if tickets:
        body += "\nIngressos individuais:\n"
        for t in tickets:
            if t.pdf_path:
                body += f"- {t.person_name} (PDF): {t.pdf_path}\n"
            elif t.png_path:
                body += f"- {t.person_name} (PNG): {t.png_path}\n"

    body += "\nApresente o QR Code dentro do ingresso na entrada.\n"

    send_email(
        to_email=to_email,
        subject=subject,
        body_text=body,
    )

    flash("E-mail enviado ✅", "success")
    return redirect(url_for("admin_purchases.admin_purchases_table"))
