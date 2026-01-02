import re
import unicodedata
from pathlib import Path
from typing import Tuple

import qrcode
from PIL import Image, ImageDraw, ImageFont
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader

from config_ticket import (
    FONT_SHOW,
    FONT_NAMES,
    FONT_SIZE_SHOW,
    FONT_SIZE_NAMES,
    LINE_SPACING_NAMES,
    SHOW_Y,
    NAMES_Y,
    TEXT_COLOR,
)


def slug_filename(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    texto = texto.lower().strip()
    texto = re.sub(r"[^a-z0-9]+", "-", texto)
    texto = re.sub(r"-{2,}", "-", texto).strip("-")
    return texto or "comprador"


def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def make_qr_image(url: str, size_px: int = 360) -> Image.Image:
    img = qrcode.make(url).convert("RGBA")
    return img.resize((size_px, size_px))


def generate_ticket_assets(
    *,
    storage_dir: Path,
    base_url: str,
    event_slug: str,
    ticket_id: int,
    show_name: str,
    buyer_name: str,
    guests_lines: list[str],
    base_image_path: Path,
    font_path: Path | None = None,  # não usado, mantido por compatibilidade
) -> Tuple[Path, Path]:

    ensure_dir(storage_dir)

    safe_name = slug_filename(buyer_name)
    base_name = f"{ticket_id:06d}-{safe_name}"

    png_path = storage_dir / event_slug / f"{base_name}.png"
    pdf_path = storage_dir / event_slug / f"{base_name}.pdf"
    ensure_dir(png_path.parent)

    # --- base
    base_img = Image.open(base_image_path).convert("RGBA")
    draw = ImageDraw.Draw(base_img)
    W, H = base_img.size

    # =========================
    # FONTE DO SHOW (KALAM)
    # =========================
    try:
        show_font = ImageFont.truetype(str(FONT_SHOW), FONT_SIZE_SHOW)
    except OSError:
        show_font = ImageFont.load_default()

    if show_name:
        bbox = draw.textbbox((0, 0), show_name, font=show_font)
        show_w = bbox[2] - bbox[0]
        x_show = (W - show_w) // 2

        draw.text(
            (x_show, SHOW_Y),
            show_name,
            font=show_font,
            fill=TEXT_COLOR,
        )

    # =========================
    # FONTE DOS NOMES (POPPINS)
    # =========================
    try:
        names_font = ImageFont.truetype(str(FONT_NAMES), FONT_SIZE_NAMES)
    except OSError:
        names_font = ImageFont.load_default()

    lines = [buyer_name] + [x for x in guests_lines if x.strip()]
    names_text = "\n".join(lines)

    bbox = draw.multiline_textbbox(
        (0, 0),
        names_text,
        font=names_font,
        align="center",
        spacing=LINE_SPACING_NAMES,
    )

    text_w = bbox[2] - bbox[0]
    x_names = (W - text_w) // 2

    draw.multiline_text(
        (x_names, NAMES_Y),
        names_text,
        font=names_font,
        fill=TEXT_COLOR,
        align="center",
        spacing=LINE_SPACING_NAMES,
    )

    # salva PNG base (QR entra depois)
    base_img.save(png_path)

    # =========================
    # PDF A4
    # =========================
    c = canvas.Canvas(str(pdf_path), pagesize=A4)
    w, h = A4

    img_reader = ImageReader(str(png_path))
    img_w, img_h = base_img.size

    scale = min((w - 80) / img_w, (h - 120) / img_h)
    draw_w = img_w * scale
    draw_h = img_h * scale

    px = (w - draw_w) / 2
    py = (h - draw_h) / 2

    c.drawImage(img_reader, px, py, draw_w, draw_h, mask="auto")
    c.showPage()
    c.save()

    return png_path, pdf_path


def paste_qr_on_png(png_path: Path, qr_img: Image.Image, *, pos=None) -> None:
    img = Image.open(png_path).convert("RGBA")
    W, H = img.size
    qW, qH = qr_img.size

    if pos is None:
        pos = ((W - qW) // 2, int(H * 0.65))

    img.paste(qr_img, pos, qr_img)
    img.save(png_path)
