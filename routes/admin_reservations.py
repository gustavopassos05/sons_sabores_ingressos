# routes/admin_reservations.py
from io import BytesIO
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import Blueprint, render_template, request, send_file, abort
from sqlalchemy import select, desc, func

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
        # ✅ lista de shows + total de pessoas por show (reserved + paid)
        show_stats = list(
            s.execute(
                select(
                    Purchase.show_name,
                    func.coalesce(func.sum(Purchase.ticket_qty), 0)
                )
                .where(Purchase.status.in_(["reserved", "paid"]))
                .group_by(Purchase.show_name)
                .order_by(Purchase.show_name.asc())
            ).all()
        )

        show_options = [name for (name, _) in show_stats]

        purchases = list(
            s.scalars(
                select(Purchase)
                .where(Purchase.status.in_(["reserved", "paid"]))
                .order_by(desc(Purchase.created_at))
                .limit(2000)
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
        show_stats=show_stats,  # ✅ novo: lista (show_name, total_pessoas)
    )

@bp_admin_reservations.get(
    "/admin/reservations/portaria.pdf",
    endpoint="admin_reservations_portaria_pdf"
)
@admin_required
def portaria_pdf():
    show_name = (request.args.get("show") or "").strip()
    if not show_name:
        abort(400, description="Parâmetro obrigatório: show")

    q = (request.args.get("q") or "").strip().lower()

    with db() as s:
        purchases = list(
            s.scalars(
                select(Purchase)
                .where(
                    Purchase.status.in_(["reserved", "paid"]),
                    Purchase.show_name == show_name
                )
                .order_by(Purchase.created_at.asc())
                .limit(5000)
            )
        )

    rows = []
    for p in purchases:
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

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=1.2*cm,
        leftMargin=1.2*cm,
        topMargin=1.2*cm,
        bottomMargin=1.2*cm,
    )

    styles = getSampleStyleSheet()
    elements = []

    generated_at = now_sp().strftime("%d/%m/%Y %H:%M")

    elements.append(Paragraph("<b>Borogodó · Sons & Sabores</b>", styles["Title"]))
    elements.append(Spacer(1, 4))
    elements.append(Paragraph(f"<b>Portaria (Reservas)</b> — {show_name}", styles["Heading2"]))
    elements.append(Paragraph(f"Gerado em: {generated_at}", styles["Normal"]))
    if q:
        elements.append(Paragraph(f"Filtro: <b>{q}</b>", styles["Normal"]))
    elements.append(Spacer(1, 10))

    table_data = [["#", "Nome", "Pessoas", "Data da reserva", "Mesa"]]
    for i, p in enumerate(rows, start=1):
        created = p.created_at.strftime("%d/%m/%Y %H:%M") if p.created_at else ""
        table_data.append([str(i), (p.buyer_name or ""), str(int(p.ticket_qty or 1)), created, ""])

    table = Table(table_data, repeatRows=1, colWidths=[0.8*cm, 8.3*cm, 1.7*cm, 4.0*cm, 2.2*cm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.grey),
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        ("TOPPADDING", (0, 0), (-1, 0), 6),
    ]))

    elements.append(table)
    elements.append(Spacer(1, 10))
    elements.append(Paragraph(f"<b>Total de reservas:</b> {len(rows)}", styles["Normal"]))
    elements.append(Paragraph(f"<b>Total de pessoas:</b> {sum(int(p.ticket_qty or 1) for p in rows)}", styles["Heading2"]))

    doc.build(elements)
    buffer.seek(0)

    ts = now_sp().strftime("%Y-%m-%d-%H-%M")
    filename = f"portaria-reservas-{_safe_filename(show_name)}-{ts}.pdf"

    return send_file(buffer, mimetype="application/pdf", as_attachment=True, download_name=filename)
