# routes/admin_settings.py
import re
import unicodedata
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from sqlalchemy import select

from db import db
from models import AdminSetting
from routes.admin_auth import admin_required

bp_admin_settings = Blueprint("admin_settings", __name__)


def slugify_show(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    texto = texto.lower().strip()
    texto = re.sub(r"[^a-z0-9]+", "-", texto)
    texto = re.sub(r"-{2,}", "-", texto).strip("-")
    return texto or "show"


def _upsert(s, key: str, value: str):
    row = s.scalar(select(AdminSetting).where(AdminSetting.key == key))
    if row:
        row.value = value
    else:
        s.add(AdminSetting(key=key, value=value))


@bp_admin_settings.get("/admin/settings")
@admin_required
def settings_get():
    shows = current_app.config.get("SHOWS", [])

    with db() as s:
        pix_key = s.scalar(select(AdminSetting).where(AdminSetting.key == "PIX_KEY"))
        whatsapp = s.scalar(select(AdminSetting).where(AdminSetting.key == "WHATSAPP_NUMBER"))
        fallback_price = s.scalar(select(AdminSetting).where(AdminSetting.key == "TICKET_PRICE_CENTS"))

        show_prices = {}
        for name in shows:
            k = f"PRICE_{slugify_show(name)}"
            row = s.scalar(select(AdminSetting).where(AdminSetting.key == k))
            show_prices[name] = (row.value if row else "")
            show_fields = [{"name": n, "slug": slugify_show(n)} for n in shows]


    return render_template(
        "admin_settings.html",
        pix_key=(pix_key.value if pix_key else ""),
        whatsapp=(whatsapp.value if whatsapp else ""),
        ticket_price_cents=(fallback_price.value if fallback_price else ""),
        shows=shows,
        show_prices=show_prices,
    )


@bp_admin_settings.post("/admin/settings")
@admin_required
def settings_post():
    pix_key = (request.form.get("pix_key") or "").strip()
    whatsapp = (request.form.get("whatsapp") or "").strip()
    ticket_price_cents = (request.form.get("ticket_price_cents") or "").strip()

    shows = current_app.config.get("SHOWS", [])

    with db() as s:
        _upsert(s, "PIX_KEY", pix_key)
        _upsert(s, "WHATSAPP_NUMBER", whatsapp)
        _upsert(s, "TICKET_PRICE_CENTS", ticket_price_cents)

        # ✅ preços por show
        for name in shows:
            field = f"price_{slugify_show(name)}"
            val = (request.form.get(field) or "").strip()
            k = f"PRICE_{slugify_show(name)}"
            _upsert(s, k, val)

    flash("Configurações salvas ✅", "success")
    return redirect(url_for("admin_settings.settings_get"))

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
