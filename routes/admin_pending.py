# routes/admin_pending.py
from datetime import datetime

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, current_app
from sqlalchemy import select, desc

from db import db
from models import Purchase, Payment, Show
from routes.admin_auth import admin_required
from app_services.email_service import send_email
from app_services.email_templates import build_reservation_email

bp_admin_pending = Blueprint("admin_pending", __name__)


@bp_admin_pending.post("/admin/confirm-reservation/<token>")
@admin_required
def confirm_reservation(token: str):
    # confirma reserva e envia email (se tiver)
    with db() as s:
        purchase = s.scalar(select(Purchase).where(Purchase.token == token))
        if not purchase:
            abort(404)

        st = (purchase.status or "").lower()
        if st not in {"reservation_pending", "reservation_pending_price"}:
            flash("Esta reserva não está pendente.", "error")
            return redirect(url_for("admin_pending.admin_pending"))

        purchase.status = "reserved"
        purchase.reservation_confirmed_at = datetime.utcnow()
        s.add(purchase)

        # pegar data do show (opcional)
        sh = s.scalar(select(Show).where(Show.name == purchase.show_name))
        date_text = (sh.date_text if sh else "")

        buyer_email = (purchase.buyer_email or "").strip()
        buyer_name = purchase.buyer_name or "Cliente"
        show_name = purchase.show_name or "Sons & Sabores"
        ticket_qty = int(purchase.ticket_qty or 1)
        purchase_id = purchase.id

    # envia e-mail fora da sessão
    if buyer_email and "@" in buyer_email:
        try:
            subject, text, html = build_reservation_email(
                buyer_name=buyer_name,
                show_name=show_name,
                date_text=date_text,
                token=token,
                ticket_qty=ticket_qty,
            )
            send_email(to_email=buyer_email, subject=subject, body_text=text, body_html=html)

            with db() as s:
                p2 = s.get(Purchase, purchase_id)
                if p2:
                    p2.reservation_email_sent_at = datetime.utcnow()
                    p2.reservation_email_sent_to = buyer_email
                    p2.reservation_email_last_error = None
                    s.add(p2)

        except Exception as e:
            with db() as s:
                p2 = s.get(Purchase, purchase_id)
                if p2:
                    p2.reservation_email_last_error = str(e)[:2000]
                    s.add(p2)

    flash("Reserva confirmada ✅ (e-mail enviado se disponível)", "success")
    return redirect(url_for("admin_pending.admin_pending"))


@bp_admin_pending.get("/admin/pending")
@admin_required
def admin_pending():
    q = (request.args.get("q") or "").strip().lower()

    with db() as s:
        purchases = list(
            s.scalars(
                select(Purchase)
                .where(
                    Purchase.status.in_([
                        "pending_payment",
                        "reservation_pending",
                        "reservation_pending_price",
                    ])
                )
                .order_by(desc(Purchase.id))
            )
        )

        rows = []
        for purchase in purchases:
            payment = s.scalar(
                select(Payment)
                .where(Payment.purchase_id == purchase.id)
                .order_by(desc(Payment.id))
            )

            hay = " ".join([
                (purchase.buyer_name or ""),
                (purchase.buyer_cpf or ""),
                (purchase.buyer_email or ""),
                (purchase.buyer_phone or ""),
                (purchase.show_name or ""),
                (purchase.token or ""),
                (purchase.status or ""),
                (payment.provider if payment else ""),
                (payment.status if payment else ""),
            ]).lower()

            if q and q not in hay:
                continue

            rows.append({
                "purchase": purchase,
                "payment": payment,  # pode ser None (reserva)
            })

    return render_template(
        "admin_pending.html",
        rows=rows,
        q=q,
    )


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

    if callable(finalize_fn):
        finalize_fn(purchase.id)

    return {"ok": True}
