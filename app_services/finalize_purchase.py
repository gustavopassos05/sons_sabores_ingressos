# app_services/finalize_purchase.py
import os
import secrets
from pathlib import Path
from sqlalchemy import select

from db import db
from models import Purchase, Ticket
from app_services.ftp_uploader import upload_file
from app_services.ticket_generator import generate_single_ticket_png  # <- já existe no seu ticket_generator.py


def _people_from_purchase(purchase: Purchase) -> list[dict]:
    people = [{"name": purchase.buyer_name, "type": "buyer"}]
    if purchase.guests_text:
        for line in purchase.guests_text.splitlines():
            name = line.strip()
            if name:
                people.append({"name": name, "type": "guest"})
    return people


def finalize_purchase_factory():
    """
    Retorna uma função finalize(purchase_id) para ser usada no webhook.
    """

    def finalize(purchase_id: int):
        with db() as s:
            purchase = s.get(Purchase, purchase_id)
            if not purchase:
                return

            # idempotência: se já tem ticket, não refaz
            existing = list(s.scalars(select(Ticket).where(Ticket.purchase_id == purchase.id)))
            if existing:
                return

            storage_dir = Path(os.getenv("STORAGE_DIR", "/tmp/sons_sabores_ingressos_storage"))
            base_image = Path(os.getenv("TICKET_BASE_IMAGE_PATH", "static/ticket_base.png"))

            # fontes (se você ainda não usa, pode apontar para static/fonts)
            font_show = Path(os.getenv("FONT_SHOW_PATH", "static/fonts/Kalam-Bold.ttf"))
            font_names = Path(os.getenv("FONT_NAMES_PATH", "static/fonts/Kalam-Bold.ttf"))

            event_slug = "sons-e-sabores"  # se você tiver event.slug no banco, dá pra puxar

            tickets_created: list[Ticket] = []

            for person in _people_from_purchase(purchase):
                token = secrets.token_urlsafe(16)

                t = Ticket(
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
                s.add(t)
                s.flush()  # garante t.id

                png_path = generate_single_ticket_png(
                    storage_dir=storage_dir,
                    event_slug=event_slug,
                    ticket_id=t.id,
                    person_name=t.person_name,
                    show_name=t.show_name,
                    base_image_path=base_image,
                    font_show_path=font_show,
                    font_names_path=font_names,
                )

                t.png_path = str(png_path)
                tickets_created.append(t)

            s.commit()

        # FTP automático (fora da sessão)
        for t in tickets_created:
            if t.png_path:
                filename = Path(t.png_path).name
                ok, info = upload_file(t.png_path, filename)
                print(f"[FTP] Ticket {t.id} -> {'OK' if ok else 'ERRO'}: {info}")

    return finalize
