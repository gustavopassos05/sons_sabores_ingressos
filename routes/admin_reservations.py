# routes/admin_reservations.py
from flask import Blueprint, render_template, request
from sqlalchemy import select, desc

from db import db
from models import Purchase
from routes.admin_auth import admin_required

bp_admin_reservations = Blueprint("admin_reservations", __name__)

@bp_admin_reservations.get("/admin/reservations")
@admin_required
def admin_reservations():
    q = (request.args.get("q") or "").strip().lower()

    with db() as s:
        purchases = list(
            s.scalars(
                select(Purchase)
                .where(Purchase.status == "reserved")
                .order_by(desc(Purchase.id))
                .limit(800)
            )
        )

    rows = []
    for p in purchases:
        hay = " ".join([
            p.buyer_name or "",
            p.buyer_cpf or "",
            p.buyer_email or "",
            p.buyer_phone or "",
            p.show_name or "",
            p.token or "",
        ]).lower()
        if q and q not in hay:
            continue
        rows.append(p)

    return render_template("admin_reservations.html", rows=rows, q=q)
