# routes/admin_tickets.py
import os
from flask import Blueprint, request, abort, render_template
from sqlalchemy import select, desc

from db import db
from models import Ticket, Purchase

bp_admin_tickets = Blueprint("admin_tickets", __name__)

def _digits(s: str) -> str:
    return "".join(c for c in (s or "") if c.isdigit())

def _check_admin():
    key = (os.getenv("ADMIN_KEY") or "").strip()
    if not key:
        raise RuntimeError("ADMIN_KEY não configurado no Render.")

    got = (request.headers.get("X-ADMIN-KEY") or request.args.get("key") or "").strip()
    if got != key:
        abort(401)

@bp_admin_tickets.get("/admin/tickets")
def admin_tickets_table():
    _check_admin()

    q = (request.args.get("q") or "").strip().lower()

    with db() as s:
        # traz tickets + purchase (join manual via purchase_id)
        tickets = list(
            s.scalars(
                select(Ticket)
                .order_by(desc(Ticket.id))
                .limit(500)
            )
        )

        # aplica filtro simples no Python (rápido de implementar)
        rows = []
        for t in tickets:
            p = s.get(Purchase, t.purchase_id) if t.purchase_id else None

            buyer = (p.buyer_name if p else t.buyer_name) or ""
            show = (t.show_name or "").strip()
            person = (t.person_name or "").strip()
            cpf = _digits(p.buyer_cpf if p else "") if p else ""

            hay = " ".join([buyer, show, person, cpf]).lower()
            if q and q not in hay:
                continue

            rows.append({"ticket": t, "purchase": p})

    return render_template(
        "admin_tickets_table.html",
        rows=rows,
        q=q,
        app_name=os.getenv("APP_NAME", "Sons & Sabores"),
    )
