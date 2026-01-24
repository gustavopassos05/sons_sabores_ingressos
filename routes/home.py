# routes/home.py
import os
from pathlib import Path

from flask import Blueprint, render_template, abort, url_for
from sqlalchemy import select, desc

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
                .order_by(desc(Show.id))
            )
        )

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
