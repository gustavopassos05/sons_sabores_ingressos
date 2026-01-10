# routes/admin_panel.py
from flask import Blueprint, render_template
from sqlalchemy import select, func

from db import db
from models import Payment, AdminSetting
from routes.admin_auth import admin_required

bp_admin_panel = Blueprint("admin_panel", __name__)

def _get_setting(s, key: str, default: str = "") -> str:
    row = s.scalar(select(AdminSetting).where(AdminSetting.key == key))
    return (row.value if row and row.value is not None else default)

@bp_admin_panel.app_context_processor
def inject_admin_badges():
    """
    Injeta contagem de pendências e configs básicas em templates.
    ⚠️ Precisa ser leve e não quebrar páginas públicas.
    """
    try:
        with db() as s:
            pending = s.scalar(
                select(func.count())
                .select_from(Payment)
                .where(Payment.provider == "manual_pix", Payment.status != "paid")
            ) or 0

            pix_key = _get_setting(s, "PIX_KEY", "")
            whatsapp = _get_setting(s, "WHATSAPP_NUMBER", "")

        return {
            "admin_pending_count": pending,
            "cfg_pix_key": pix_key,
            "cfg_whatsapp": whatsapp,
        }
    except Exception:
        # não derruba o site público se der qualquer problema no admin/db
        return {
            "admin_pending_count": 0,
            "cfg_pix_key": "",
            "cfg_whatsapp": "",
        }

@bp_admin_panel.get("/admin", endpoint="home")
@admin_required
def admin_home():
    return render_template("admin_home.html")
