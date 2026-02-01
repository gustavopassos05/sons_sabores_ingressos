# routes/home.py
import os
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import Blueprint, render_template, abort, url_for
from sqlalchemy import select, desc as sa_desc

from db import db
from models import Event, Show

bp_home = Blueprint("home", __name__)

SAO_PAULO_TZ = ZoneInfo("America/Sao_Paulo")

def now_sp():
    return datetime.now(SAO_PAULO_TZ).replace(tzinfo=None)

def _static_show_image_url(show_slug: str) -> str:
    """
    Tenta achar uma imagem em static/shows/<slug>.(jpg|jpeg|png|webp)
    Se não existir, volta placeholder.
    """
    for ext in ("jpg", "jpeg", "png", "webp"):
        rel = f"shows/{show_slug}.{ext}"
        if (Path("static") / rel).exists():
            return url_for("static", filename=rel)

    return url_for("static", filename="shows/placeholder.jpg")

def _parse_show_datetime(date_text: str) -> datetime | None:
    """
    Converte date_text em datetime.

    Suporta exemplos:
      - "30/01/2026 às 20:30"
      - "30/01/2026 20:30"
      - "30/01 20h30"
      - "Sexta 30/01 20h"
      - "30 de janeiro - 20h"
      - "30 de janeiro de 2026 - 20h30"
      - "30/01/2026" (assume 00:00)
      - "30/01" (assume 00:00)

    Heurística sem ano:
      - assume ano atual
      - se cair muito no passado (>60 dias), assume ano seguinte
    """
    raw = (date_text or "").strip()
    if not raw:
        return None

    s = raw.lower().strip()

    # remove palavras comuns / ruído
    s = s.replace("às", " ")
    s = s.replace("as", " ")
    s = s.replace("—", "-").replace("–", "-")
    s = s.replace("  ", " ")

    # remove dia da semana (se existir)
    weekdays = ["segunda", "terça", "terca", "quarta", "quinta", "sexta", "sábado", "sabado", "domingo"]
    for wd in weekdays:
        s = re.sub(rf"\b{wd}\b", " ", s)
    s = re.sub(r"\s+", " ", s).strip()

    # normaliza horas:
    # 20h30 -> 20:30
    s = re.sub(r"(\d{1,2})h(\d{2})", r"\1:\2", s)
    # 20h -> 20:00
    s = re.sub(r"(\d{1,2})h\b", r"\1:00", s)
    s = re.sub(r"\s+", " ", s).strip()

    # meses por extenso pt-br
    months = {
        "janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4,
        "maio": 5, "junho": 6, "julho": 7, "agosto": 8,
        "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
    }

    # helper: decide ano quando falta
    def _infer_year(day: int, month: int) -> int:
        now = now_sp()
        y = now.year
        try:
            candidate = datetime(y, month, day)
        except Exception:
            return y
        # se ficou "muito" no passado, assume próximo ano
        if (now - candidate).days > 60:
            return y + 1
        return y

    # 1) dd/mm/yyyy [hh:mm]
    m = re.search(r"(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{4})(?:\s+(\d{1,2}):(\d{2}))?", s)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hh = int(m.group(4)) if m.group(4) else 0
        mm = int(m.group(5)) if m.group(5) else 0
        try:
            return datetime(y, mo, d, hh, mm)
        except Exception:
            return None

    # 2) dd/mm [hh:mm]  (sem ano)
    m = re.search(r"(\d{1,2})[\/\-](\d{1,2})(?:\s+(\d{1,2}):(\d{2}))?", s)
    if m:
        d, mo = int(m.group(1)), int(m.group(2))
        hh = int(m.group(3)) if m.group(3) else 0
        mm = int(m.group(4)) if m.group(4) else 0
        y = _infer_year(d, mo)
        try:
            return datetime(y, mo, d, hh, mm)
        except Exception:
            return None

    # 3) dd de <mes> [de yyyy] [ - hh:mm ]
    # exemplos: "30 de janeiro - 20:00", "30 de janeiro de 2026 20:30"
    m = re.search(
        r"(\d{1,2})\s+de\s+([a-zç]+)(?:\s+de\s+(\d{4}))?(?:\s*[- ]\s*(\d{1,2}):(\d{2}))?",
        s
    )
    if m:
        d = int(m.group(1))
        mon_name = (m.group(2) or "").strip()
        mo = months.get(mon_name)
        if not mo:
            return None
        y = int(m.group(3)) if m.group(3) else _infer_year(d, mo)
        hh = int(m.group(4)) if m.group(4) else 0
        mm = int(m.group(5)) if m.group(5) else 0
        try:
            return datetime(y, mo, d, hh, mm)
        except Exception:
            return None

    return None

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
                .order_by(sa_desc(Show.id))
            )
        )

    now = now_sp()
    cards = []

    for sh in shows:
        dt = _parse_show_datetime(getattr(sh, "date_text", ""))  # pode ser None
        is_past = (dt is not None and dt < now)

        title = (getattr(sh, "title", None) or "").strip() or sh.name
        description = (getattr(sh, "description", None) or "").strip() or "Reserve seu lugar e venha viver essa noite com a gente."
        subtitle = "Ao vivo no Borogodó"

        img_url = (getattr(sh, "image_url", None) or "").strip()
        if not img_url:
            img_url = _static_show_image_url(sh.slug)

        cards.append({
            "name": title,
            "original_name": sh.name,
            "slug": sh.slug,
            "date_text": sh.date_text,
            "dt": dt,                 # ✅ novo
            "is_past": is_past,       # ✅ novo
            "price_cents": sh.price_cents,
            "requires_ticket": int(sh.requires_ticket or 0),
            "subtitle": subtitle,
            "desc": description,
            "img": img_url,
        })

    # ✅ ORDEM:
    # 1) futuros (dt asc)
    # 2) passados (dt desc, mais recente primeiro)
    # 3) sem data (por último)
    def _sort_key(c):
        dt = c["dt"]
        if dt is None:
            return (3, datetime.max.timestamp())
        if c["is_past"]:
            return (2, -dt.timestamp())
        return (1, dt.timestamp())

    cards.sort(key=_sort_key)

    return render_template(
        "home.html",
        event=ev,
        cards=cards,
        app_name=os.getenv("APP_NAME", "Sons & Sabores"),
    )
