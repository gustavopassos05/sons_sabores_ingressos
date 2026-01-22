# routes/admin_pending.py
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, current_app
from sqlalchemy import select, desc

from db import db
from models import Purchase, Payment
from routes.admin_auth import admin_required
from app_services.email_service import send_email
from app_services.email_templates import build_reservation_email


bp_admin_pending = Blueprint("admin_pending", __name__)

@bp_admin_pending.post("/admin/confirm-reservation/<token>")
@admin_required
def confirm_reservation(token: str):
    from app_services.email_templates import build_reservation_email
    from app_services.email_service import send_email

    with db() as s:
        purchase = s.scalar(select(Purchase).where(Purchase.token == token))
        if not purchase:
            abort(404)

        if (purchase.status or "") != "reservation_pending":
            flash("Esta reserva não está pendente.", "error")
            return redirect(url_for("admin_pending.admin_pending"))

        purchase.status = "reserved"
        purchase.reservation_confirmed_at = datetime.utcnow()
        s.add(purchase)

    # envia e-mail (se tiver e-mail)
    if purchase.buyer_email and "@" in purchase.buyer_email:
        subject, text, html = build_reservation_email(
            buyer_name=purchase.buyer_name,
            show_name=purchase.show_name,
            date_text="",  # (se quiser, carregamos do show)
            token=purchase.token,
            ticket_qty=purchase.ticket_qty or 1,
        )
        try:
            send_email(to_email=purchase.buyer_email, subject=subject, body_text=text, body_html=html)

            with db() as s:
                p2 = s.scalar(select(Purchase).where(Purchase.id == purchase.id))
                p2.reservation_email_sent_at = datetime.utcnow()
                p2.reservation_email_sent_to = purchase.buyer_email
                p2.reservation_email_last_error = None
                s.add(p2)
        except Exception as e:
            with db() as s:
                p2 = s.scalar(select(Purchase).where(Purchase.id == purchase.id))
                p2.reservation_email_last_error = str(e)[:2000]
                s.add(p2)

    flash("Reserva confirmada ✅ (e-mail enviado se disponível)", "success")
    return redirect(url_for("admin_pending.admin_pending"))

@bp_admin_pending.get("/admin/pending")
@admin_required
def admin_pending():
    q = (request.args.get("q") or "").strip().lower()

    with db() as s:
        purchases = list(s.scalars(select(Purchase).order_by(desc(Purchase.id)).limit(300)))

        rows = []
        for p in purchases:
            pay = s.scalar(
                select(Payment).where(Payment.purchase_id == p.id).order_by(desc(Payment.id))
            )
            if not pay:
                continue
            if (pay.status or "").lower() == "paid":
                continue

            hay = " ".join([(p.buyer_name or ""), (p.buyer_cpf or ""), (p.show_name or ""), (p.token or "")]).lower()
            if q and q not in hay:
                continue

            rows.append({"purchase": p, "payment": pay})

    return render_template("admin_pending.html", rows=rows, q=q)

@bp_admin_pending.post("/admin/mark-paid/<purchase_token>")
@admin_required
def admin_mark_paid(purchase_token: str):
    finalize_fn = current_app.extensions.get("finalize_purchase")

    with db() as s:
        purchase = s.scalar(select(Purchase).where(Purchase.token == purchase_token))
        if not purchase:
            abort(404)

        payment = s.scalar(
            select(Payment).where(Payment.purchase_id == purchase.id).order_by(desc(Payment.id))
        )
        if not payment:
            abort(404)

        payment.status = "paid"
        payment.paid_at = datetime.utcnow()
        purchase.status = "paid"

        s.add(payment)
        s.add(purchase)
        # seu db() já comita automático

    if callable(finalize_fn):
        finalize_fn(purchase.id)

    return {"ok": True}
