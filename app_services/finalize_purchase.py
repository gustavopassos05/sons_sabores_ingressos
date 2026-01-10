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
    slug_filename,
)
from app_services.ftp_uploader import upload_file


def _names_from_purchase(p: Purchase) -> List[str]:
    """
    Comprador + convidados (aceita guests_text com linhas e também ',' e ';').
    """
    names: List[str] = []
    if (p.buyer_name or "").strip():
        names.append(p.buyer_name.strip())

    raw = (p.guests_text or "").strip()
    if not raw:
        return names

    tmp: List[str] = []
    for line in raw.splitlines():
        line = (line or "").strip()
        if not line:
            continue
        parts = [x.strip() for x in line.replace(";", ",").split(",") if x.strip()]
        tmp.extend(parts)

    names.extend(tmp)
    return names


def _make_single_pdf_from_png(png_path: Path, pdf_path: Path) -> None:
    """
    PDF individual (1 página) a partir do PNG (já com QR).
    """
    from PIL import Image
    img = Image.open(png_path).convert("RGB")
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(pdf_path)


def _make_pdf_from_pngs(png_paths: List[Path], pdf_path: Path) -> None:
    """
    PDF geral (várias páginas) a partir dos PNGs (já com QR).
    """
    from PIL import Image
    if not png_paths:
        raise RuntimeError("Nenhum PNG para gerar PDF geral.")
    imgs = [Image.open(p).convert("RGB") for p in png_paths]
    first, rest = imgs[0], imgs[1:]
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    first.save(pdf_path, save_all=True, append_images=rest)


def _make_zip(files: List[Path], zip_path: Path) -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            if f and Path(f).exists():
                z.write(f, arcname=Path(f).name)


def finalize_purchase_factory() -> Callable[[int], None]:
    """
    Ao confirmar pagamento (webhook/admin):
    - cria 1 Ticket por pessoa
    - gera PNG/PDF individual com QR individual (/ticket/<ticket.token>)
    - faz upload FTP e salva URL pública em Ticket.png_path / Ticket.pdf_path
    - também gera bundle (PDF geral + ZIP) e salva em Payment.tickets_pdf_url / tickets_zip_url
    """

    def finalize(purchase_id: int) -> None:
        storage_dir: Path = current_app.config["STORAGE_DIR"]
        base_image_path: Path = current_app.config["TICKET_BASE_IMAGE_PATH"]

        font_show_path = Path(os.getenv("TICKET_FONT_SHOW", "static/fonts/Kalam-Bold.ttf")).resolve()
        font_names_path = Path(os.getenv("TICKET_FONT_NAME", "static/fonts/Kalam-Bold.ttf")).resolve()

        public_base = (os.getenv("FTP_PUBLIC_BASE") or "").rstrip("/")
        if not public_base:
            raise RuntimeError("FTP_PUBLIC_BASE não configurado (URL pública dos arquivos no HostGator).")

        base_url = (current_app.config.get("BASE_URL") or "").rstrip("/")
        if not base_url:
            raise RuntimeError("BASE_URL não configurado.")

        with db() as s:
            purchase: Optional[Purchase] = s.get(Purchase, purchase_id)
            if not purchase:
                return

            # ✅ pega payment PAID primeiro (evita pegar pending mais recente)
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

            # só roda se realmente pago
            if (purchase.status or "").lower() != "paid" or (payment.status or "").lower() != "paid":
                return

            # ✅ idempotência: se já tem bundle, considera finalizado
            if getattr(payment, "tickets_pdf_url", None) or getattr(payment, "tickets_zip_url", None):
                return

            # evento
            ev: Optional[Event] = s.get(Event, purchase.event_id)
            event_slug = (ev.slug if ev else "evento")
            show_name = purchase.show_name or ""

            names = _names_from_purchase(purchase)
            if not names:
                names = ["Convidado"]

            # pasta local por compra
            local_dir = (storage_dir / "tickets" / purchase.token).resolve()
            local_dir.mkdir(parents=True, exist_ok=True)

            png_paths: List[Path] = []
            pdf_paths: List[Path] = []

            # ✅ gera ingressos individuais
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

                # ✅ QR individual do ticket
                qr_target_url = f"{base_url}/ticket/{t.token}"
                qr_img = make_qr_image(qr_target_url, size_px=360)

                # gera PNG com show + nome
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

                # cola QR no PNG
                paste_qr_on_png(png_path, qr_img, margin=40)

                # gera PDF individual (1 página) a partir do PNG (já com QR)
                pdf_path = (png_path.parent / (png_path.stem + ".pdf")).resolve()
                _make_single_pdf_from_png(png_path, pdf_path)

                png_paths.append(png_path)
                pdf_paths.append(pdf_path)

                # ✅ nome do arquivo baseado em nome+sobreNome (slug) + id para evitar colisão
                safe_name = slug_filename(person_name)
                png_remote = f"{safe_name}-{t.id}.png"
                pdf_remote = f"{safe_name}-{t.id}.pdf"

                ok_png, info_png = upload_file(png_path, png_remote)
                if not ok_png:
                    raise RuntimeError(str(info_png))

                ok_pdf, info_pdf = upload_file(pdf_path, pdf_remote)
                if not ok_pdf:
                    raise RuntimeError(str(info_pdf))

                # salva URL pública no Ticket (links individuais)
                t.png_path = f"{public_base}/{png_remote}"
                t.pdf_path = f"{public_base}/{pdf_remote}"

                s.add(t)

            print("[FINALIZE] FTP_PUBLIC_BASE raw:", os.getenv("FTP_PUBLIC_BASE"))
            print("[FINALIZE] public_base used:", public_base)
            print("[FINALIZE] pdf_all_remote:", pdf_all_remote)
            print("[FINALIZE] tickets_pdf_url:", f"{public_base}/{pdf_all_remote}")


            # ✅ bundle (PDF geral + ZIP)
            pdf_all_path = (local_dir / f"{purchase.token}-ingressos.pdf").resolve()
            _make_pdf_from_pngs(png_paths, pdf_all_path)

            zip_path = (local_dir / f"{purchase.token}-ingressos.zip").resolve()
            _make_zip([pdf_all_path] + pdf_paths + png_paths, zip_path)

            pdf_all_remote = f"{purchase.token}-ingressos.pdf"
            zip_remote = f"{purchase.token}-ingressos.zip"

            ok_pdf_all, info_pdf_all = upload_file(pdf_all_path, pdf_all_remote)
            if not ok_pdf_all:
                raise RuntimeError(str(info_pdf_all))

            ok_zip, info_zip = upload_file(zip_path, zip_remote)
            if not ok_zip:
                raise RuntimeError(str(info_zip))

            payment.tickets_pdf_url = f"{public_base}/{pdf_all_remote}"
            payment.tickets_zip_url = f"{public_base}/{zip_remote}"
            payment.tickets_generated_at = datetime.utcnow()

            s.add(payment)
            s.commit()

            print("[FINALIZE] purchase", purchase.id)
            print("[FINALIZE] tickets:", len(names))
            print("[FINALIZE] bundle PDF:", payment.tickets_pdf_url)
            print("[FINALIZE] bundle ZIP:", payment.tickets_zip_url)

    return finalize
