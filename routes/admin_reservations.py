# routes/admin_reservations.py
from io import BytesIO
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import Blueprint, render_template, request, send_file, abort
from sqlalchemy import select, desc

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib import colors

from db import db
from models import Purchase
from routes.admin_auth import admin_required

bp_admin_reservations = Blueprint("admin_reservations", __name__)

SAO_PAULO_TZ = ZoneInfo("America/Sao_Paulo")
def now_sp():
    return datetime.now(SAO_PAULO_TZ).replace(tzinfo=None)

def _safe_filename(s: str) -> str:
    s = (s or "").strip().lower()
    s = "".join(ch if ch.isalnum() else "-" for ch in s)
    while "--" in s:
        s = s.replace("--", "-")
    return s.strip("-") or "show"

@bp_admin_reservations.get("/admin/reservations")
@admin_required
def admin_reservations():
    q = (request.args.get("q") or "").strip().lower()
    show_selected = (request.args.get("show") or "").strip()

    with db() as s:
        # shows que têm reservas confirmadas
        show_options = list(
            s.scalars(
                select(Purchase.show_name)
                .where(Purchase.status.in_(["reserved", "paid"]))
                .distinct()
                .order_by(Purchase.show_name.asc())
            )
        )

        purchases = list(
            s.scalars(
                select(Purchase)
                .where(Purchase.status.in_(["reserved", "paid"]), Purchase.show_name == show_name)
                .order_by(desc(Purchase.created_at))
                .limit(5000)
            )
        )


    rows = []
    total_reservas = 0
    total_pessoas = 0

    for p in purchases:
        if show_selected and (p.show_name or "") != show_selected:
            continue

        hay = " ".join([
            p.buyer_name or "",
            p.buyer_cpf or "",
            p.buyer_email or "",
            p.buyer_phone or "",
            p.token or "",
        ]).lower()

        if q and q not in hay:
            continue

        rows.append(p)
        total_reservas += 1
        total_pessoas += int(p.ticket_qty or 1)

    return render_template(
        "admin_reservations.html",
        rows=rows,
        q=q,
        show_options=show_options,
        show_selected=show_selected,
        total_reservas=total_reservas,
        total_pessoas=total_pessoas,
    )
