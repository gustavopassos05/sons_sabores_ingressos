# app_services/finalize_purchase.py
import os
import secrets
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional

from flask import current_app
from sqlalchemy import select

from db import db
from models import Purchase, Payment, Ticket, Event

from app_services.ticket_generator import (
    generate_single_ticket_png,
    make_qr_image,
    paste_qr_on_png,
)
from app_services.ftp_uploader import upload_file


def _digits(s: str) -> str:
    return "".join(c for c in (s or "") if c.isdigit())


def _names_from_purchase(p: Purchase) -> List[str]:
    names: List[str] = []
    if (p.buyer_name or "").strip():
        names.append(p.buyer_name.strip())
    guests = (p.guests_text or "").splitlines()
    guests = [g.strip() for g in guests if g.strip()]
    names.extend(guests)
    return names


def _make_pdf_from_pngs(png_paths: List[Path], pdf_path: Path) -> None:
    from PIL import Image
    if not png_paths:
        raise RuntimeError("Nenhum PNG para gerar PDF.")
    imgs = [Image.open(p).convert("RGB") for p in png_paths]
    first, rest = imgs[0], imgs[1:]
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    first.save(pdf_path, save_all=True, append_images=rest)


def _make_zip(files: List[Path], zip_path: Path) -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(f, arcname=f.name)


def finalize_purchase_factory() -> Callable[[int], None]:
    def finalize(purchase_id: int) -> None:
        storage_dir: Path = current_app.config["STORAGE_DIR"]
        base_image_path: Path = current_app.config["TICKET_BASE_IMAGE_PATH"]

        font_show_path = Path(os.getenv("TICKET_FONT_SHOW", "static/fonts/Kalam-Bold.ttf")).resolve()
        font_names_path = Path(os.getenv("TICKET_FONT_NAME", "static/fonts/Kalam-Bold.ttf")).resolve()

        public_base = (os.getenv("FTP_PUBLIC_BASE") or "").rstrip("/")
        if not public_base:
            raise RuntimeError("FTP_PUBLIC_BASE não configurado (URL pública dos arquivos no HostGator).")

        remote_prefix = (os.getenv("TICKETS_REMOTE_PREFIX") or "ingressos").strip().strip("/")

        base_url = (current_app.config.get("BASE_URL") or "").rstrip("/")
        if not base_url:
            raise RuntimeError("BASE_URL não configurado.")

        with db() as s:
            purchase: Optional[Purchase] = s.get(Purchase, purchase_id)
            if not purchase:
                return

            payment: Optional[Payment] = s.scalar(
                select(Payment)
                .where(Payment.purchase_id == purchase.id)
                .order_by(Payment.id.desc())
            )
            if not payment:
                return

            # só roda se realmente pago
            if (purchase.status or "").lower() != "paid" or (payment.status or "").lower() != "paid":
                return

            # idempotência: se já gerou link, sai
            if getattr(payment, "tickets_pdf_url", None):
                return

            ev: Optional[Event] = s.get(Event, purchase.event_id)
            event_slug = (ev.slug if ev else "evento")
            show_name = purchase.show_name or ""

            names = _names_from_purchase(purchase)
            if not names:
                names = ["Convidado"]

            # pasta local
            local_dir = (storage_dir / "tickets" / purchase.token).resolve()
            local_dir.mkdir(parents=True, exist_ok=True)

            # QR aponta pra página pública do ingresso/compra
            # ajuste se sua rota real for diferente
            qr_target_url = f"{base_url}/purchase/{purchase.token}"
            qr_img = make_qr_image(qr_target_url, size_px=360)

            png_paths: List[Path] = []
            created_tickets: List[Ticket] = []

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
                    token=secrets.token_urlsafe(18),  # token individual do ticket
                    status="issued",
                    issued_at=datetime.utcnow(),
                )
                s.add(t)
                s.flush()  # garante t.id

                png_path = generate_single_ticket_png(
                    storage_dir=local_dir,
                    event_slug=event_slug,
                    ticket_id=t.id,               # id real do ticket
                    person_name=person_name,
                    show_name=show_name,
                    base_image_path=base_image_path,
                    font_show_path=font_show_path,
                    font_names_path=font_names_path,
                )
                paste_qr_on_png(png_path, qr_img, margin=40)

                png_paths.append(png_path)
                t.png_path = str(png_path)
                created_tickets.append(t)

            # gera PDF com todos
            pdf_path = (local_dir / "ingressos.pdf").resolve()
            _make_pdf_from_pngs(png_paths, pdf_path)

            # salva pdf_path em todos tickets (mesmo pdf)
            for t in created_tickets:
                t.pdf_path = str(pdf_path)

            # zip
            zip_path = (local_dir / "ingressos.zip").resolve()
            _make_zip([pdf_path] + png_paths, zip_path)

            # upload FTP
            remote_folder = f"{remote_prefix}/{purchase.token}"
            pdf_remote = f"{remote_folder}/{pdf_path.name}"
            zip_remote = f"{remote_folder}/{zip_path.name}"

            ok_pdf, info_pdf = upload_file(pdf_path, pdf_remote)
            if not ok_pdf:
                raise RuntimeError(str(info_pdf))

            ok_zip, info_zip = upload_file(zip_path, zip_remote)
            if not ok_zip:
                raise RuntimeError(str(info_zip))

            payment.tickets_pdf_url = f"{public_base}/{pdf_remote}"
            payment.tickets_zip_url = f"{public_base}/{zip_remote}"
            payment.tickets_generated_at = datetime.utcnow()

            s.commit()

    return finalize
