# app_services/finalize_purchase.py
import os
import secrets
from pathlib import Path
from typing import List, Dict, Any

from sqlalchemy import select

from db import db
from models import Purchase, Ticket, Event
from app_services.ftp_uploader import upload_file
from app_services.ticket_generator import (
    generate_single_ticket_png,
    make_qr_image,
    paste_qr_on_png,
)

def _digits(s: str) -> str:
    return "".join(c for c in (s or "") if c.isdigit())

def finalize_purchase_factory(app):
    """
    Registra em app.extensions["finalize_purchase"] uma função finalize(purchase_id),
    que gera tickets e faz upload FTP (PNG).
    """

    storage_dir = Path(app.config["STORAGE_DIR"])
    base_image_path = Path(app.config["TICKET_BASE_IMAGE_PATH"])

    # fontes (ajuste se seus caminhos forem outros)
    font_show_path = Path(os.getenv("TICKET_FONT_SHOW", "static/fonts/Kalam-Bold.ttf"))
    font_names_path = Path(os.getenv("TICKET_FONT_NAMES", "static/fonts/Kalam-Bold.ttf"))

    def finalize(purchase_id: int) -> bool:
        # vamos coletar os pngs pra FTP fora da sessão
        png_files: List[str] = []
        token_publico = None
        event_slug = "evento"

        with db() as s:
            purchase = s.get(Purchase, purchase_id)
            if not purchase:
                return False

            token_publico = purchase.token

            # idempotência: se já tem ingressos, não gera de novo
            existing = list(s.scalars(select(Ticket).where(Ticket.purchase_id == purchase.id)))
            if existing:
                # ainda tenta FTP se faltar (opcional)
                for t in existing:
                    if t.png_path:
                        png_files.append(t.png_path)
                return True

            ev = s.get(Event, purchase.event_id)
            if ev and ev.slug:
                event_slug = ev.slug

            # lista de pessoas (comprador + acompanhantes)
            people: List[Dict[str, Any]] = [{"name": purchase.buyer_name, "type": "buyer"}]
            if purchase.guests_text:
                for line in purchase.guests_text.splitlines():
                    name = line.strip()
                    if name:
                        people.append({"name": name, "type": "guest"})

            # cria tickets e gera PNG com QR
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

                # link do QR (ajuste se sua rota pública for outra)
                base_url = (os.getenv("BASE_URL") or app.config.get("BASE_URL") or "").rstrip("/")
                qr_url = f"{base_url}/t/{ticket.token}" if base_url else f"/t/{ticket.token}"
                qr_img = make_qr_image(qr_url, size_px=360)

                png_path = generate_single_ticket_png(
                    storage_dir=storage_dir,
                    event_slug=event_slug,
                    ticket_id=ticket.id,
                    person_name=ticket.person_name,
                    show_name=ticket.show_name,
                    base_image_path=base_image_path,
                    font_show_path=font_show_path,
                    font_names_path=font_names_path,
                )
                paste_qr_on_png(png_path, qr_img)

                ticket.png_path = str(png_path)
                ticket.pdf_path = None  # (se quiser PDF depois, a gente implementa)
                png_files.append(str(png_path))

            s.commit()

        # FTP automático (fora do db)
        for path_str in png_files:
            try:
                filename = Path(path_str).name
                ok, info = upload_file(path_str, filename)
                if not ok:
                    print(f"[FTP] Falha no upload {filename}: {info}")
                else:
                    print(f"[FTP] OK {filename}: {info}")
            except Exception as e:
                print(f"[FTP] Erro inesperado: {e}")

        return True

    # registra no app para o webhook usar
    app.extensions["finalize_purchase"] = finalize
    return finalize
