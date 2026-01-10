# routes/admin_panel.py
import os
from flask import Blueprint, request, abort, render_template, redirect, url_for

bp_admin_panel = Blueprint("admin_panel", __name__)

def _check_admin():
    key = (os.getenv("ADMIN_KEY") or "").strip()
    if not key:
        raise RuntimeError("ADMIN_KEY não configurado no Render.")
    got = (request.headers.get("X-ADMIN-KEY") or request.args.get("key") or "").strip()
    if got != key:
        abort(401)

def _get_setting(s, key: str, default: str = "") -> str:
    row = s.scalar(select(AdminSetting).where(AdminSetting.key == key))
    return (row.value if row and row.value is not None else default)

@bp_admin_panel.app_context_processor
def inject_admin_badges():
    # injeta em todos templates do app, mas só mostra no admin_base
    pending = 0
    with db() as s:
        # pendências = pagamentos manuais pendentes (ajuste se quiser incluir outros providers)
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

@bp_admin_panel.get("/admin")
@admin_required
def admin_home():
    return render_template("admin_home.html")
