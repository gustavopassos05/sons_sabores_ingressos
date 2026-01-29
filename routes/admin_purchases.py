# routes/admin_purchases.py
from datetime import datetime
from zoneinfo import ZoneInfo
from flask import Blueprint, render_template, request, abort, flash, redirect, url_for
from sqlalchemy import select, desc, func
from io import BytesIO
import csv
from io import StringIO
from flask import Response
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib import colors
from db import db
from models import Purchase, Payment, Ticket
from routes.admin_auth import admin_required
from sqlalchemy import select, desc, func
from models import Purchase, Payment, Ticket

from app_services.email_service import send_email
from app_services.email_templates import build_tickets_email


bp_admin_purchases = Blueprint("admin_purchases", __name__)

SAO_PAULO_TZ = ZoneInfo("America/Sao_Paulo")

def now_sp():
    return datetime.now(SAO_PAULO_TZ).replace(tzinfo=None)

@bp_admin_purchases.get("/admin/purchases")
@admin_required
def admin_purchases_table():
    q = (request.args.get("q") or "").strip().lower()
    show_selected = (request.args.get("show") or "").strip()

    with db() as s:
        # lista de shows disponíveis (apenas compras pagas)
        show_options = list(
            s.scalars(
                select(Purchase.show_name)
                .join(Payment, Payment.purchase_id == Purchase.id)
                .where(Payment.status == "paid")
                .distinct()
                .order_by(Purchase.show_name.asc())
            )
        )

        stmt = (
            select(Purchase, Payment)
            .join(Payment, Payment.purchase_id == Purchase.id)
            .where(Payment.status == "paid")
            .order_by(desc(Purchase.id))
            .limit(800)
        )

        pairs = list(s.execute(stmt).all())

        rows = []
        for p, pay in pairs:
            if show_selected and (p.show_name or "") != show_selected:
                continue

            hay = " ".join([
                (p.buyer_name or ""),
                (p.buyer_cpf or ""),
                (p.show_name or ""),
                (p.token or ""),
                (pay.provider if pay else ""),
                (pay.status if pay else ""),
                (p.buyer_email or ""),
                (p.buyer_phone or ""),
            ]).lower()

            if q and q not in hay:
                continue

            ticket_count = s.scalar(
                select(func.count()).select_from(Ticket).where(Ticket.purchase_id == p.id)
            ) or 0

            rows.append({
                "purchase": p,
                "payment": pay,
                "ticket_count": ticket_count,
            })

    return render_template(
        "admin_purchases_table.html",
        rows=rows,
        q=q,
        show_options=show_options,
        show_selected=show_selected,
    )


@bp_admin_purchases.post("/admin/purchases/send-email/<int:purchase_id>")
@admin_required
def admin_send_purchase_email(purchase_id: int):
    to_email = (request.form.get("to_email") or "").strip()
    if not to_email or "@" not in to_email:
        flash("Informe um e-mail válido.", "error")
        return redirect(url_for("admin_purchases.admin_purchases_table"))

    # carrega purchase/payment/tickets
    with db() as s:
        purchase = s.get(Purchase, purchase_id)
        if not purchase:
            abort(404)

        payment_paid = s.scalar(
            select(Payment)
            .where(Payment.purchase_id == purchase.id, Payment.status == "paid")
            .order_by(desc(Payment.id))
        )
        payment = payment_paid or s.scalar(
            select(Payment)
            .where(Payment.purchase_id == purchase.id)
            .order_by(desc(Payment.id))
        )

        tickets = list(
            s.scalars(
                select(Ticket).where(Ticket.purchase_id == purchase.id).order_by(Ticket.id.asc())
            )
        )

    pdf_all_url = (payment.tickets_pdf_url or "") if payment else ""
    zip_url = (payment.tickets_zip_url or "") if payment else ""

    ticket_rows = []
    for t in tickets:
        ticket_rows.append({
            "name": t.person_name,
            "pdf": t.pdf_path or "",
            "png": t.png_path or "",
        })

    subject, text, html = build_tickets_email(
        buyer_name=purchase.buyer_name or "Cliente",
        show_name=purchase.show_name or "Sons & Sabores",
        total_brl=((payment.amount_cents or 0) / 100) if payment else 0.0,
        token=purchase.token,
        ticket_qty=int(purchase.ticket_qty or len(tickets) or 1),                 # ✅
        unit_price_cents=int(purchase.ticket_unit_price_cents or 0),              # ✅
        pdf_all_url=pdf_all_url,
        zip_url=zip_url,
        tickets=ticket_rows,
    )


    try:
        send_email(
            to_email=to_email,
            subject=subject,
            body_text=text,
            body_html=html,
        )

        # marca como enviado no payment mais recente
        with db() as s:
            pay_db = s.scalar(
                select(Payment)
                .where(Payment.purchase_id == purchase.id)
                .order_by(desc(Payment.id))
            )
            if pay_db:
                pay_db.tickets_email_sent_at = datetime.utcnow()
                pay_db.tickets_email_sent_to = to_email
                pay_db.tickets_email_last_error = None
                s.add(pay_db)

        flash("E-mail enviado ✅", "success")

    except Exception as e:
        with db() as s:
            pay_db = s.scalar(
                select(Payment)
                .where(Payment.purchase_id == purchase.id)
                .order_by(desc(Payment.id))
            )
            if pay_db:
                pay_db.tickets_email_last_error = str(e)[:2000]
                s.add(pay_db)

        flash(f"Falha ao enviar e-mail: {e}", "error")

    return redirect(url_for("admin_purchases.admin_purchases_table"))

@bp_admin_purchases.post("/admin/purchases/send-email-buyer/<int:purchase_id>")
@admin_required
def admin_send_purchase_email_buyer(purchase_id: int):
    # Busca compra/pagamento/tickets
    with db() as s:
        purchase = s.get(Purchase, purchase_id)
        if not purchase:
            abort(404)

        to_email = (purchase.buyer_email or "").strip()
        if not to_email or "@" not in to_email:
            flash("Esta compra não tem e-mail válido do comprador.", "error")
            return redirect(url_for("admin_purchases.admin_purchases_table"))

        payment_paid = s.scalar(
            select(Payment)
            .where(Payment.purchase_id == purchase.id, Payment.status == "paid")
            .order_by(desc(Payment.id))
        )
        payment = payment_paid or s.scalar(
            select(Payment)
            .where(Payment.purchase_id == purchase.id)
            .order_by(desc(Payment.id))
        )

        tickets = list(
            s.scalars(select(Ticket).where(Ticket.purchase_id == purchase.id).order_by(Ticket.id.asc()))
        )

    pdf_all_url = (payment.tickets_pdf_url or "") if payment else ""
    zip_url = (payment.tickets_zip_url or "") if payment else ""

    ticket_rows = [{"name": t.person_name, "pdf": t.pdf_path or "", "png": t.png_path or ""} for t in tickets]

    subject, text, html = build_tickets_email(
        buyer_name=purchase.buyer_name or "Cliente",
        show_name=purchase.show_name or "Sons & Sabores",
        total_brl=((payment.amount_cents or 0) / 100) if payment else 0.0,
        token=purchase.token,
        ticket_qty=int(purchase.ticket_qty or len(tickets) or 1),                 # ✅
        unit_price_cents=int(purchase.ticket_unit_price_cents or 0),              # ✅
        pdf_all_url=pdf_all_url,
        zip_url=zip_url,
        tickets=ticket_rows,
    )


    try:
        send_email(to_email=to_email, subject=subject, body_text=text, body_html=html)

        # marca status enviado
        with db() as s:
            pay_db = s.scalar(
                select(Payment)
                .where(Payment.purchase_id == purchase.id)
                .order_by(desc(Payment.id))
            )
            if pay_db:
                pay_db.tickets_email_sent_at = datetime.utcnow()
                pay_db.tickets_email_sent_to = to_email
                pay_db.tickets_email_last_error = None
                s.add(pay_db)

        flash("E-mail enviado para o comprador ✅", "success")

    except Exception as e:
        with db() as s:
            pay_db = s.scalar(
                select(Payment)
                .where(Payment.purchase_id == purchase.id)
                .order_by(desc(Payment.id))
            )
            if pay_db:
                pay_db.tickets_email_last_error = str(e)[:2000]
                s.add(pay_db)

        flash(f"Falha ao enviar e-mail: {e}", "error")

    return redirect(url_for("admin_purchases.admin_purchases_table"))
@bp_admin_purchases.get("/admin/purchases/export.pdf")
@admin_required
def admin_purchases_export_pdf():
    q = (request.args.get("q") or "").strip().lower()
    show_filter = (request.args.get("show") or "").strip().lower()

    with db() as s:
        pairs = list(
            s.execute(
                select(Purchase, Payment)
                .join(Payment, Payment.purchase_id == Purchase.id)
                .where(Payment.status == "paid")
                .order_by(desc(Purchase.created_at))
            ).all()
        )

        rows = []
        total_geral = 0

        for p, pay in pairs:
            if show_filter and show_filter not in (p.show_name or "").lower():
                continue

            hay = " ".join([
                p.buyer_name or "",
                p.buyer_cpf or "",
                p.show_name or "",
                p.token or "",
                p.buyer_email or "",
            ]).lower()

            if q and q not in hay:
                continue

            total = (pay.amount_cents or 0) / 100
            total_geral += total
            rows.append((p, pay, total))

    buffer = BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=2*cm,
        leftMargin=2*cm,
        topMargin=2*cm,
        bottomMargin=2*cm
    )

    styles = getSampleStyleSheet()
    elements = []

    # Cabeçalho
    elements.append(Paragraph("<b>Borogodó · Sons & Sabores</b>", styles["Title"]))
    elements.append(Spacer(1, 6))
    elements.append(Paragraph(
        f"Relatório de compras confirmadas<br/>Gerado em: {now_sp().strftime('%d/%m/%Y %H:%M')}",
        styles["Normal"]
    ))
    elements.append(Spacer(1, 12))

    if show_filter:
        elements.append(Paragraph(f"<b>Filtro por show:</b> {show_filter}", styles["Normal"]))
        elements.append(Spacer(1, 12))

    # Tabela
    table_data = [[
        "Show", "Comprador", "Email", "Qtd", "Valor unit.", "Total", "Data"
    ]]

    for p, pay, total in rows:
        unit = (p.ticket_unit_price_cents or 0) / 100
        table_data.append([
            p.show_name,
            p.buyer_name,
            p.buyer_email or "",
            str(p.ticket_qty),
            f"R$ {unit:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
            f"R$ {total:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
            p.created_at.strftime("%d/%m/%Y %H:%M") if p.created_at else "",
        ])

    table = Table(table_data, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.lightgrey),
        ("GRID", (0,0), (-1,-1), 0.5, colors.grey),
        ("FONT", (0,0), (-1,0), "Helvetica-Bold"),
        ("ALIGN", (3,1), (-1,-1), "CENTER"),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("BOTTOMPADDING", (0,0), (-1,0), 8),
    ]))

    elements.append(table)
    elements.append(Spacer(1, 14))

    # Total geral
    elements.append(Paragraph(
        f"<b>Total geral:</b> R$ {total_geral:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
        styles["Heading2"]
    ))

    doc.build(elements)
    buffer.seek(0)

    ts = now_sp().strftime("%Y-%m-%d-%H-%M")
    filename = f"compras-confirmadas-{ts}.pdf"

    return send_file(
        buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename
    )

@bp_admin_purchases.get("/admin/purchases/summary")
@admin_required
def admin_purchases_summary():
    with db() as s:
        pairs = list(
            s.execute(
                select(Purchase, Payment)
                .join(Payment, Payment.purchase_id == Purchase.id)
                .where(Payment.status == "paid")
                .order_by(desc(Purchase.id))
            ).all()
        )

    summary = {}
    for p, pay in pairs:
        key = (p.show_name or "—").strip()
        if key not in summary:
            summary[key] = {"show": key, "pessoas": 0, "vendas": 0, "total": 0.0}
        summary[key]["vendas"] += 1
        summary[key]["pessoas"] += int(p.ticket_qty or 1)
        summary[key]["total"] += float((pay.amount_cents or 0) / 100)

    # ordena por total desc
    items = sorted(summary.values(), key=lambda x: x["total"], reverse=True)

    return render_template("admin_purchases_summary.html", items=items)

@bp_admin_purchases.post("/admin/purchases/mark-paid/<token>")
@admin_required
def admin_purchases_mark_paid(token: str):
    """
    Alias para confirmar pagamento a partir da tela de COMPRAS.
    Reaproveita a lógica existente do admin_pending.
    """
    return redirect(url_for("admin_pending.admin_mark_paid", token=token))
def _safe_filename(s: str) -> str:
    s = (s or "").strip().lower()
    s = "".join(ch if ch.isalnum() else "-" for ch in s)
    while "--" in s:
        s = s.replace("--", "-")
    return s.strip("-") or "show"

def _parse_guests_text(guests_text: str) -> list[str]:
    raw = (guests_text or "").strip()
    if not raw:
        return []
    out = []
    for line in raw.splitlines():
        line = (line or "").strip()
        if not line:
            continue
        parts = [p.strip() for p in line.replace(";", ",").split(",") if p.strip()]
        out.extend(parts)
    return out

@bp_admin_purchases.get("/admin/purchases/portaria.pdf")
@admin_required
def admin_portaria_pdf():
    """
    PDF "Portaria" por show (somente pagamentos confirmados).
    1 linha por pessoa (comprador + acompanhantes), exibindo CPF e telefone do comprador.
    Uso:
      /admin/purchases/portaria.pdf?show=<NOME EXATO DO SHOW>
    """
    show_name = (request.args.get("show") or "").strip()
    if not show_name:
        abort(400, description="Parâmetro obrigatório: show")

    q = (request.args.get("q") or "").strip().lower()

    with db() as s:
        pairs = list(
            s.execute(
                select(Purchase, Payment)
                .join(Payment, Payment.purchase_id == Purchase.id)
                .where(
                    Payment.status == "paid",
                    Purchase.show_name == show_name
                )
                .order_by(desc(Purchase.created_at))
                .limit(5000)
            ).all()
        )

    # Expande 1 linha por pessoa
    people_rows = []
    for p, pay in pairs:
        hay = " ".join([
            p.buyer_name or "",
            p.buyer_cpf or "",
            p.buyer_email or "",
            p.buyer_phone or "",
            p.token or "",
            (p.guests_text or "")
        ]).lower()
        if q and q not in hay:
            continue

        buyer = (p.buyer_name or "").strip() or "Comprador"
        guests = _parse_guests_text(p.guests_text)

        buyer_cpf = (p.buyer_cpf or "").strip()
        buyer_phone = (p.buyer_phone or "").strip()

        # comprador
        people_rows.append({
            "person": buyer,
            "kind": "Comprador",
            "buyer_cpf": buyer_cpf,
            "buyer_phone": buyer_phone,
            "token": p.token,
            "created": p.created_at.strftime("%d/%m/%Y %H:%M") if p.created_at else "",
        })

        # acompanhantes
        for g in guests:
            people_rows.append({
                "person": g,
                "kind": "Acompanhante",
                "buyer_cpf": buyer_cpf,
                "buyer_phone": buyer_phone,
                "token": p.token,
                "created": p.created_at.strftime("%d/%m/%Y %H:%M") if p.created_at else "",
            })

    # ----- PDF -----
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
    elements.append(Paragraph(f"<b>Lista Portaria</b> — {show_name}", styles["Heading2"]))
    elements.append(Paragraph(f"Gerado em: {generated_at}", styles["Normal"]))
    if q:
        elements.append(Paragraph(f"Filtro: <b>{q}</b>", styles["Normal"]))
    elements.append(Spacer(1, 10))

    # tabela: 1 linha por pessoa
    table_data = [[
        "#", "Nome", "Tipo", "CPF (comprador)", "Telefone (comprador)", "Token", "Check-in"
    ]]

    for i, r in enumerate(people_rows, start=1):
        table_data.append([
            str(i),
            r["person"],
            r["kind"],
            r["buyer_cpf"],
            r["buyer_phone"],
            r["token"],
            "☐",
        ])

    # A4 útil ~ 18.6cm (com margens 1.2cm de cada lado)
    table = Table(
        table_data,
        repeatRows=1,
        colWidths=[0.7*cm, 5.8*cm, 1.9*cm, 2.6*cm, 3.1*cm, 3.0*cm, 1.5*cm]
    )
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.grey),
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        ("TOPPADDING", (0, 0), (-1, 0), 6),
    ]))

    elements.append(table)
    elements.append(Spacer(1, 10))

    total_pessoas = len(people_rows)
    total_compras = len(pairs)

    elements.append(Paragraph(f"<b>Total de compras pagas:</b> {total_compras}", styles["Normal"]))
    elements.append(Paragraph(f"<b>Total de pessoas (portaria):</b> {total_pessoas}", styles["Heading2"]))

    doc.build(elements)
    buffer.seek(0)

    ts = now_sp().strftime("%Y-%m-%d-%H-%M")
    filename = f"portaria-{_safe_filename(show_name)}-{ts}.pdf"

    return send_file(
        buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename
    )
