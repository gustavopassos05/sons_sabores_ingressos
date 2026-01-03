# app_services/finalize_purchase.py
import os
import secrets
from pathlib import Path
from typing import Callable

from sqlalchemy import select

from db import db
from models import Purchase, Ticket, Event


def finalize_purchase_factory(app) -> Callable[[int], None]:
    """
    Retorna a função finalize_purchase(purchase_id)
    """

    storage_dir = Path(app.config["STORAGE_DIR"])
    storage_dir.mkdir(parents=True, exist_ok=True)

    def finalize_purchase(purchase_id: int) -> None:
        with db() as s:
            purchase = s.get(Purchase, purchase_id)
            if not purchase:
                return

            # evita gerar tickets duplicados
            existing = s.scalar(
                select(Ticket).where(Ticket.purchase_id == purchase.id)
            )
            if existing:
                return

            event = s.get(Event, purchase.event_id)
            if not event:
                return

            # 1️⃣ cria lista de pessoas
            people = []

            # comprador
            people.append({
                "name": purchase.buyer_name,
                "type": "buyer"
            })

            # convidados
            if purchase.guests_text:
                for line in purchase.guests_text.splitlines():
                    name = line.strip()
                    if name:
                        people.append({
                            "name": name,
                            "type": "guest"
                        })

            # 2️⃣ gera tickets
            for person in people:
                token = secrets.token_urlsafe(18)

                ticket = Ticket(
                    event_id=event.id,
                    purchase_id=purchase.id,
                    show_name=purchase.show_name,
                    buyer_name=purchase.buyer_name,
                    buyer_email=purchase.buyer_email,
                    buyer_phone=purchase.buyer_phone,
                    person_name=person["name"],
                    person_type=person["type"],
                    token=token,
                    status="issued",
                )

                s.add(ticket)

            s.commit()

        # 3️⃣ (opcional) geração de imagem / PDF / FTP
        # aqui você pode plugar:
        # - gerar QR Code
        # - gerar PNG
        # - gerar PDF
        # - enviar por FTP
        #
        # Exemplo futuro:
        # generate_ticket_assets(purchase_id)

    return finalize_purchase
