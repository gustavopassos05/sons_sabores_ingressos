# app_services/ticket_generator.py
import re
import unicodedata
from io import BytesIO
from pathlib import Path
from typing import Tuple

import qrcode
from PIL import Image, ImageDraw, ImageFont


def slug_filename(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    texto = texto.lower().strip()
    texto = re.sub(r"[^a-z0-9]+", "-", texto)
    texto = re.sub(r"-{2,}", "-", texto).strip("-")
    return texto or "pessoa"


def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def make_qr_image(url: str, size_px: int = 360) -> Image.Image:
    img = qrcode.make(url).convert("RGBA")
    return img.resize((size_px, size_px))


def generate_single_ticket_png(
    *,
    storage_dir: Path,
    event_slug: str,
    ticket_id: int,
    person_name: str,
    show_name: str,
    base_image_path: Path,
    font_show_path: Path,
    font_names_path: Path,
    show_y: int = 350,
    names_y: int = 480,
    font_size_show: int = 56,
    font_size_name: int = 72,
    line_spacing: int = 12,
) -> Path:
    ensure_dir(storage_dir)
    out_dir = storage_dir / event_slug
    ensure_dir(out_dir)

    safe = slug_filename(person_name)
    png_path = out_dir / f"{ticket_id:06d}-{safe}.png"

    base_img = Image.open(base_image_path).convert("RGBA")
    draw = ImageDraw.Draw(base_img)
    W, H = base_img.size

    # fontes
    try:
        show_font = ImageFont.truetype(str(font_show_path), font_size_show)
    except Exception:
        show_font = ImageFont.load_default()

    try:
        name_font = ImageFont.truetype(str(font_names_path), font_size_name)
    except Exception:
        name_font = ImageFont.load_default()

    # SHOW centralizado
    if show_name:
        bbox = draw.textbbox((0, 0), show_name, font=show_font)
        sw = bbox[2] - bbox[0]
        x = (W - sw) // 2
        draw.text((x, show_y), show_name, font=show_font, fill=(0, 0, 0, 255))

    # NOME (1 pessoa por ticket)
    name_text = (person_name or "").strip()
    if name_text:
        bbox = draw.textbbox((0, 0), name_text, font=name_font)
        nw = bbox[2] - bbox[0]
        x = (W - nw) // 2
        draw.text((x, names_y), name_text, font=name_font, fill=(0, 0, 0, 255))

    base_img.save(png_path)
    return png_path


def paste_qr_on_png(png_path: Path, qr_img: Image.Image, *, pos=None, margin=40) -> None:
    img = Image.open(png_path).convert("RGBA")
    W, H = img.size
    qW, qH = qr_img.size

    if pos is None:
        x = max(0, W - qW - margin)
        y = margin
        pos = (x, y)

    img.paste(qr_img, pos, qr_img)
    img.save(png_path)
