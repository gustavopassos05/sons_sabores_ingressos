# routes/admin_pending.py
from datetime import datetime

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, current_app
from sqlalchemy import select, desc
import csv
from io import StringIO
from flask import Response
from db import db
from models import Purchase, Payment, Show
from routes.admin_auth import admin_required
from app_services.email_service import send_email
from app_services.email_templates import build_reservation_email

bp_admin_pending = Blueprint("admin_pending", __name__)


@bp_admin_pending.post("/admin/confirm-reservation/<token>")
@admin_required
def confirm_reservation(token: str):
    """
    Confirma reserva e envia e-mail ao cliente (se tiver).
    Agora inclui acompanhantes no e-mail (guests_text).
    """

    # 1) Atualiza compra para RESERVED e coleta dados necessários (tudo dentro da sessão)
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

        # pega data do show (opcional)
        sh = s.scalar(select(Show).where(Show.name == purchase.show_name))
        date_text = (sh.date_text if sh else "") or ""

        buyer_email = (purchase.buyer_email or "").strip()
        buyer_name = (purchase.buyer_name or "Cliente").strip()
        show_name = (purchase.show_name or "Sons & Sabores").strip()
        ticket_qty = int(purchase.ticket_qty or 1)

        # ✅ acompanhantes (uma pessoa por linha em guests_text)
        guests = [g.strip() for g in (purchase.guests_text or "").splitlines() if g.strip()]

        purchase_id = purchase.id

        # ✅ garante persistência do status reserved antes de enviar e-mail
        s.commit()

    # 2) Envia e-mail fora da sessão (não travar transação por SMTP)
    if buyer_email and "@" in buyer_email:
        try:
            subject, text, html = build_reservation_email(
                buyer_name=buyer_name,
                show_name=show_name,
                date_text=date_text,
                token=token,
                ticket_qty=ticket_qty,
                guests=guests,  # ✅ AQUI: acompanha no e-mail
            )

            send_email(
                to_email=buyer_email,
                subject=subject,
                body_text=text,
                body_html=html,
            )

            # marca como enviado
            with db() as s:
                p2 = s.get(Purchase, purchase_id)
                if p2:
                    p2.reservation_email_sent_at = datetime.utcnow()
                    p2.reservation_email_sent_to = buyer_email
                    p2.reservation_email_last_error = None
                    s.add(p2)
                    s.commit()

        except Exception as e:
            # registra erro (sem quebrar a confirmação)
            with db() as s:
                p2 = s.get(Purchase, purchase_id)
                if p2:
                    p2.reservation_email_last_error = str(e)[:2000]
                    s.add(p2)
                    s.commit()

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

@bp_admin_pending.post("/admin/reject/<token>")
@admin_required
def admin_reject(token: str):
    reason = (request.form.get("reason") or "").strip()

    with db() as s:
        purchase = s.scalar(select(Purchase).where(Purchase.token == token))
        if not purchase:
            abort(404)

        st = (purchase.status or "").lower()
        if st in {"paid", "cancelled"}:
            flash("Esta reserva não pode ser rejeitada.", "error")
            return redirect(url_for("admin_pending.admin_pending"))

        purchase.status = "cancelled"
        purchase.rejection_reason = reason or "Não foi possível confirmar a reserva."
        purchase.rejected_at = now_sp()
        s.add(purchase)
        s.commit()

        buyer_email = (purchase.buyer_email or "").strip()
        buyer_name = (purchase.buyer_name or "Cliente").strip()
        show_name = (purchase.show_name or "Sons & Sabores").strip()

    # e-mail pro cliente (best-effort)
    if buyer_email and "@" in buyer_email:
        try:
            subject = f"Reserva não confirmada — {show_name}"
            body = (
                f"Oi, {buyer_name}!\n\n"
                "A gente recebeu sua reserva, mas infelizmente não conseguimos confirmar desta vez.\n\n"
                f"Show: {show_name}\n"
                f"Motivo: {purchase.rejection_reason}\n"
                f"Token: {token}\n\n"
                "Se quiser tentar outra data/atração, é só fazer uma nova reserva pelo site.\n\n"
                "Com carinho,\n"
                "Borogodó · Sons & Sabores"
            )
            send_email(to_email=buyer_email, subject=subject, body_text=body)
        except Exception as e:
            current_app.logger.warning("[REJECT EMAIL] falhou token=%s err=%s", token, e)

    flash("Reserva rejeitada ❌", "success")
    return redirect(url_for("admin_pending.admin_pending"))


@bp_admin_pending.post("/admin/mark-paid/<purchase_token>")
@admin_required
def admin_mark_paid(purchase_token: str):
    finalize_fn = current_app.extensions.get("finalize_purchase")

    with db() as s:
        purchase = s.scalar(select(Purchase).where(Purchase.token == purchase_token))
        if not purchase:
            abort(404)

        payment = s.scalar(select(Payment).where(Payment.purchase_id == purchase.id).order_by(desc(Payment.id)))
        if not payment:
            abort(404)

        payment.status = "paid"
        payment.paid_at = now_sp()   # ✅ São Paulo
        purchase.status = "paid"

        s.add(payment)
        s.add(purchase)
        s.commit()
        purchase_id = purchase.id

    if callable(finalize_fn):
        finalize_fn(purchase_id)

    return {"ok": True}

@bp_admin_pending.get("/admin/pending/export.csv")
@admin_required
def admin_pending_export_csv():
    q = (request.args.get("q") or "").strip().lower()

    with db() as s:
        purchases = list(
            s.scalars(
                select(Purchase)
                .where(Purchase.status.in_(["pending_payment", "reservation_pending", "reservation_pending_price"]))
                .order_by(desc(Purchase.id))
            )
        )

        rows = []
        for p in purchases:
            pay = s.scalar(select(Payment).where(Payment.purchase_id == p.id).order_by(desc(Payment.id)))

            hay = " ".join([
                (p.buyer_name or ""),
                (p.buyer_cpf or ""),
                (p.buyer_email or ""),
                (p.buyer_phone or ""),
                (p.show_name or ""),
                (p.token or ""),
                (p.status or ""),
                (pay.provider if pay else ""),
                (pay.status if pay else ""),
            ]).lower()

            if q and q not in hay:
                continue

            rows.append((p, pay))

    out = StringIO()
    w = csv.writer(out)
    w.writerow(["Show", "Status", "Comprador", "CPF", "Email", "Telefone", "Pessoas", "Token", "Valor_total"])
    for p, pay in rows:
        total = (pay.amount_cents / 100) if (pay and pay.amount_cents) else 0
        w.writerow([
            p.show_name, p.status, p.buyer_name, p.buyer_cpf, p.buyer_email, p.buyer_phone,
            p.ticket_qty, p.token, f"{total:.2f}".replace(".", ",")
        ])

    resp = Response(out.getvalue(), mimetype="text/csv; charset=utf-8")
    resp.headers["Content-Disposition"] = "attachment; filename=pending.csv"
    return resp
