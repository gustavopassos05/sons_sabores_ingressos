# app_services/finalize_purchase.py
import os
import secrets
from pathlib import Path
from sqlalchemy import select

from db import db
from models import Purchase, Ticket
from app_services.ftp_uploader import upload_file
from app_services import ticket_generator



def finalize_purchase(purchase_id: int):
    """
    Finaliza uma compra paga:
    - Gera ingressos (1 por pessoa)
    - Gera PNG/PDF
    - Envia PNGs para FTP automaticamente
    """

    with db() as s:
        purchase = s.get(Purchase, purchase_id)
        if not purchase:
            return

        # 🔒 idempotência: se já tem ingressos, não faz de novo
        existing = list(s.scalars(select(Ticket).where(Ticket.purchase_id == purchase.id)))
        if existing:
            return

        # 📌 monta lista de pessoas
        people = []

        # comprador
        people.append({
            "name": purchase.buyer_name,
            "type": "buyer",
        })

        # acompanhantes
        if purchase.guests_text:
            for line in purchase.guests_text.splitlines():
                name = line.strip()
                if name:
                    people.append({
                        "name": name,
                        "type": "guest",
                    })

        tickets_created: list[Ticket] = []

        for person in people:
            token = secrets.token_urlsafe(16)

            ticket = Ticket(
                event_id=purchase.event_id,
                purchase_id=purchase.id,
                show_name=purchase.show_name,
                buyer_name=purchase.buyer_name,
                buyer_email=purchase.buyer_email,
                buyer_phone=purchase.buyer_phone,
                person_name=person["name"],
                person_type=person["type"],
                token=token,
            )
            s.add(ticket)
            s.flush()  # garante ticket.id

            # 🎫 gera imagens
            png_path, pdf_path = generate_ticket_images(ticket)

            ticket.png_path = png_path
            ticket.pdf_path = pdf_path

            tickets_created.append(ticket)

        s.commit()

    # 🚀 FTP AUTOMÁTICO (fora da sessão)
    for t in tickets_created:
        if t.png_path:
            filename = Path(t.png_path).name
            ok, info = upload_file(t.png_path, filename)
            if not ok:
                print(f"[FTP] Falha no upload do ingresso {t.id}: {info}")
            else:
                print(f"[FTP] Ingresso {t.id} enviado: {info}")
