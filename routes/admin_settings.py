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

