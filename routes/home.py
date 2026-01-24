# routes/home.py
import os
from flask import Blueprint, render_template, abort, url_for
from sqlalchemy import select, desc

from db import db
from models import Event, Show

bp_home = Blueprint("home", __name__)

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

    # Textos (por enquanto aqui; depois a gente move pra banco/admin)
    show_copy = {
        # exemplo por slug:
        # "jimmy-duchowny-quarteto": {
        #   "subtitle": "Jazz ao vivo",
        #   "desc": "Uma noite de jazz com petiscos e clima Borogodó.",
        # },
    }

    # monta cards já com imagem e textos
    cards = []
    for sh in shows:
        meta = show_copy.get(sh.slug, {})
        cards.append({
            "name": sh.name,
            "slug": sh.slug,
            "date_text": sh.date_text,
            "price_cents": sh.price_cents,
            "requires_ticket": int(sh.requires_ticket or 0),
            "subtitle": meta.get("subtitle", "Ao vivo no Borogodó"),
            "desc": meta.get("desc", "Reserve seu lugar e venha viver essa noite com a gente."),
            # imagem: static/shows/<slug>.jpg
            "img": url_for("static", filename=f"shows/{sh.slug}.jpg"),
        })

    return render_template(
        "home.html",
        event=ev,
        cards=cards,
        app_name=os.getenv("APP_NAME", "Sons & Sabores"),
    )
