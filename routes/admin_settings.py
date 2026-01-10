# routes/admin_settings.py
from flask import Blueprint, render_template, request, redirect, url_for, flash
from sqlalchemy import select

from db import db
from models import AdminSetting
from routes.admin_auth import admin_required

bp_admin_settings = Blueprint("admin_settings", __name__)

def _upsert(s, key: str, value: str):
    row = s.scalar(select(AdminSetting).where(AdminSetting.key == key))
    if row:
        row.value = value
    else:
        s.add(AdminSetting(key=key, value=value))

@bp_admin_settings.get("/admin/settings")
@admin_required
def settings_get():
    with db() as s:
        pix_key = s.scalar(select(AdminSetting).where(AdminSetting.key == "PIX_KEY"))
        whatsapp = s.scalar(select(AdminSetting).where(AdminSetting.key == "WHATSAPP_NUMBER"))
        price = s.scalar(select(AdminSetting).where(AdminSetting.key == "TICKET_PRICE_CENTS"))
    return render_template(
        "admin_settings.html",
        pix_key=(pix_key.value if pix_key else ""),
        whatsapp=(whatsapp.value if whatsapp else ""),
        ticket_price_cents=(price.value if price else ""),
    )

@bp_admin_settings.post("/admin/settings")
@admin_required
def settings_post():
    pix_key = (request.form.get("pix_key") or "").strip()
    whatsapp = (request.form.get("whatsapp") or "").strip()
    ticket_price_cents = (request.form.get("ticket_price_cents") or "").strip()

    with db() as s:
        _upsert(s, "PIX_KEY", pix_key)
        _upsert(s, "WHATSAPP_NUMBER", whatsapp)
        _upsert(s, "TICKET_PRICE_CENTS", ticket_price_cents)

    flash("Configurações salvas ✅", "success")
    return redirect(url_for("admin_settings.settings_get"))
