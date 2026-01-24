# routes/home.py
import os
from pathlib import Path
from flask import Blueprint, render_template, abort, url_for
from sqlalchemy import select, desc as sa_desc
import re
from db import db
from models import Event, Show

bp_home = Blueprint("home", __name__)


def _static_show_image_url(show_slug: str) -> str:
    """
    Tenta achar uma imagem em static/shows/<slug>.(jpg|jpeg|png|webp)
    Se não existir, volta placeholder.
    """
    for ext in ("jpg", "jpeg", "png", "webp"):
        rel = f"shows/{show_slug}.{ext}"
        if Path("static") / rel:
            # Atenção: Path("static")/rel sempre "existe" como objeto.
            # Então vamos checar existência real no filesystem:
            if (Path("static") / rel).exists():
                return url_for("static", filename=rel)

    return url_for("static", filename="shows/placeholder.jpg")


@bp_home.get("/")
def home():
    event_slug = (os.getenv("DEFAULT_EVENT_SLUG") or "sons-e-sabores").strip()

    with db() as s:
        ev = s.scalar(select(Event).where(Event.slug == event_slug))
        if not ev:
            abort(404)

        shows = list(
            s.scalars(
                select(Show)
                .where(Show.is_active == 1)
            )
        )

        # ✅ ordena por data (as que não têm data válida vão pro fim)
        shows.sort(key=lambda sh: (_parse_show_datetime(getattr(sh, "date_text", "")) is None,
                                   _parse_show_datetime(getattr(sh, "date_text", "")) or datetime.max))


    # fallback opcional por slug (se quiser override específico)
    show_copy = {
        # "mark-lambert-jimmy-duchouny": {
        #   "subtitle": "Jazz ao vivo · Sons & Sabores",
        #   "desc": "Uma noite especial com Mark Lambert e Jimmy Duchouny…",
        # },
    }

    cards = []
    for sh in shows:
        meta = show_copy.get(sh.slug, {})

        # ✅ prioridade: admin -> fallback -> default
        title = (getattr(sh, "title", None) or "").strip() or sh.name
        desc = (getattr(sh, "description", None) or "").strip() or meta.get(
            "desc",
            "Reserve seu lugar e venha viver essa noite com a gente."
        )
        subtitle = meta.get("subtitle", "Ao vivo no Borogodó")

        # ✅ imagem: prioridade image_url do admin; senão tenta static por slug (jpg/jpeg/png/webp)
        img_url = (getattr(sh, "image_url", None) or "").strip()
        if not img_url:
            img_url = _static_show_image_url(sh.slug)

        cards.append({
            "name": title,  # <- card usa title
            "original_name": sh.name,
            "slug": sh.slug,
            "date_text": sh.date_text,
            "price_cents": sh.price_cents,
            "requires_ticket": int(sh.requires_ticket or 0),
            "subtitle": subtitle,
            "desc": desc,
            "img": img_url,
        })

    return render_template(
        "home.html",
        event=ev,
        cards=cards,
        app_name=os.getenv("APP_NAME", "Sons & Sabores"),
    )

def _parse_show_datetime(date_text: str) -> datetime | None:
    """
    Tenta converter date_text em datetime.
    Aceita exemplos:
      - "24/01/2026 19:00"
      - "24/01/2026 19h"
      - "24/01/2026"
      - "24-01-2026 19:00"
      - "2026-01-24 19:00"
    Se não der, retorna None.
    """
    raw = (date_text or "").strip()
    if not raw:
        return None

    # normaliza separadores
    s = raw.lower().replace("h", ":").replace("–", "-").replace("—", "-")
    s = re.sub(r"\s+", " ", s).strip()

    # tenta extrair padrões comuns
    candidates = []

    # dd/mm/yyyy [hh:mm]
    m = re.search(r"(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{4})(?:\s+(\d{1,2})(?::(\d{2}))?)?", s)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hh = int(m.group(4)) if m.group(4) else 0
        mm = int(m.group(5)) if m.group(5) else 0
        try:
            return datetime(y, mo, d, hh, mm)
        except Exception:
            pass

    # yyyy-mm-dd [hh:mm]
    m = re.search(r"(\d{4})[\/\-](\d{1,2})[\/\-](\d{1,2})(?:\s+(\d{1,2})(?::(\d{2}))?)?", s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hh = int(m.group(4)) if m.group(4) else 0
        mm = int(m.group(5)) if m.group(5) else 0
        try:
            return datetime(y, mo, d, hh, mm)
        except Exception:
            pass

    return None
