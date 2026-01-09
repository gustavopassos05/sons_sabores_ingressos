# app_services/finalize_purchase.py
import os
import secrets
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional

from flask import current_app
from sqlalchemy import select

from db import db
from models import Purchase, Payment, Ticket, Event
from app_services.ticket_generator import generate_single_ticket_png, make_qr_image, paste_qr_on_png
from app_services.ftp_uploader import upload_file


def _names_from_purchase(p: Purchase) -> List[str]:
    names: List[str] = []
    if (p.buyer_name or "").strip():
        names.append(p.buyer_name.strip())
    guests = (p.guests_text or "").splitlines()
    guests = [g.strip() for g in guests if g.strip()]
    names.extend(guests)
    return names


def _make_single_pdf_from_png(png_path: Path, pdf_path: Path) -> None:
    from PIL import Image
    img = Image.open(png_path).convert("RGB")
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(pdf_path)


def finalize_purchase_factory() -> Callable[[int], None]:
    def finalize(purchase_id: int) -> None:
        storage_dir: Path = current_app.config["STORAGE_DIR"]
        base_image_path: Path = current_app.config["TICKET_BASE_IMAGE_PATH"]

        font_show_path = Path(os.getenv("TICKET_FONT_SHOW", "static/fonts/Kalam-Bold.ttf")).resolve()
        font_names_path = Path(os.getenv("TICKET_FONT_NAME", "static/fonts/Kalam-Bold.ttf")).resolve()

        public_base = (os.getenv("FTP_PUBLIC_BASE") or "").rstrip("/")
        if not public_base:
            raise RuntimeError("FTP_PUBLIC_BASE não configurado.")

        base_url = (current_app.config.get("BASE_URL") or "").rstrip("/")
        if not base_url:
            raise RuntimeError("BASE_URL não configurado.")

        with db() as s:
            purchase: Optional[Purchase] = s.get(Purchase, purchase_id)
            if not purchase:
                return

            # ✅ pegue o payment PAID primeiro (evita pegar pending mais recente)
            payment_paid: Optional[Payment] = s.scalar(
                select(Payment)
                .where(Payment.purchase_id == purchase.id, Payment.status == "paid")
                .order_by(Payment.id.desc())
            )
            payment: Optional[Payment] = payment_paid or s.scalar(
                select(Payment)
                .where(Payment.purchase_id == purchase.id)
                .order_by(Payment.id.desc())
            )
            if not payment:
                return

            if (purchase.status or "").lower() != "paid" or (payment.status or "").lower() != "paid":
                return

            # ✅ se já tem tickets com URL, não refaz (idempotência)
            already = s.scalar(
                select(Ticket)
                .where(Ticket.purchase_id == purchase.id, Ticket.png_path.isnot(None))
                .order_by(Ticket.id.asc())
            )
            if already:
                return

            ev: Optional[Event] = s.get(Event, purchase.event_id)
            event_slug = (ev.slug if ev else "evento")
            show_name = purchase.show_name or ""

            names = _names_from_purchase(purchase) or ["Convidado"]

            # pasta local
            local_dir = (storage_dir / "tickets" / purchase.token).resolve()
            local_dir.mkdir(parents=True, exist_ok=True)

            # QR aponta para a página pública da compra
            qr_target_url = f"{base_url}/purchase/{purchase.token}"
            qr_img = make_qr_image(qr_target_url, size_px=360)

            for idx, person_name in enumerate(names, start=1):
                t = Ticket(
                    event_id=purchase.event_id,
                    purchase_id=purchase.id,
                    show_name=show_name,
                    buyer_name=purchase.buyer_name,
                    buyer_email=purchase.buyer_email,
                    buyer_phone=purchase.buyer_phone,
                    person_name=person_name,
                    person_type="buyer" if idx == 1 else "guest",
                    token=secrets.token_urlsafe(18),
                    status="issued",
                    issued_at=datetime.utcnow(),
                )
                s.add(t)
                s.flush()  # garante t.id

                # gera PNG
                png_path = generate_single_ticket_png(
                    storage_dir=local_dir,
                    event_slug=event_slug,
                    ticket_id=t.id,
                    person_name=person_name,
                    show_name=show_name,
                    base_image_path=base_image_path,
                    font_show_path=font_show_path,
                    font_names_path=font_names_path,
                )
                paste_qr_on_png(png_path, qr_img, margin=40)

                # gera PDF 1 página (por pessoa)
                pdf_path = (png_path.parent / (png_path.stem + ".pdf")).resolve()
                _make_single_pdf_from_png(png_path, pdf_path)

                # nomes remotos (sem pastas — mais seguro no HostGator)
                png_remote = f"{purchase.token}-ticket-{t.id}.png"
                pdf_remote = f"{purchase.token}-ticket-{t.id}.pdf"

                ok_png, info_png = upload_file(png_path, png_remote)
                if not ok_png:
                    raise RuntimeError(str(info_png))

                ok_pdf, info_pdf = upload_file(pdf_path, pdf_remote)
                if not ok_pdf:
                    raise RuntimeError(str(info_pdf))

                # ✅ salva URL pública no banco (links individuais)
                t.png_path = f"{public_base}/{png_remote}"
                t.pdf_path = f"{public_base}/{pdf_remote}"

                s.add(t)

            s.commit()

    return finalize
