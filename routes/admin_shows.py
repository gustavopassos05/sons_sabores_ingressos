# routes/admin_shows.py
import re
import unicodedata

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from sqlalchemy import select, desc

from db import db
from models import Show
from routes.admin_auth import admin_required

bp_admin_shows = Blueprint("admin_shows", __name__)


def brl_to_cents(value: str) -> int | None:
    """
    Converte "50,00" -> 5000
    Aceita: "50", "50,0", "50,00", "1.234,56", "R$ 50,00"
    Retorna None se vazio.
    """
    raw = (value or "").strip()
    if not raw:
        return None

    raw = raw.replace("R$", "").strip()
    raw = raw.replace(".", "").replace(",", ".")  # 1.234,56 -> 1234.56

    try:
        v = float(raw)
    except Exception:
        return None

    cents = int(round(v * 100))
    return cents if cents > 0 else None


def slugify(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    texto = texto.lower().strip()
    texto = re.sub(r"[^a-z0-9]+", "-", texto)
    texto = re.sub(r"-{2,}", "-", texto).strip("-")
    return texto or "show"


@bp_admin_shows.get("/admin/shows")
@admin_required
def shows_list():
    with db() as s:
        shows = list(s.scalars(select(Show).order_by(desc(Show.id))))
    return render_template("admin_shows.html", shows=shows)


@bp_admin_shows.post("/admin/shows/create")
@admin_required
def shows_create():
    name = (request.form.get("name") or "").strip()
    date_text = (request.form.get("date_text") or "").strip()

    # ✅ agora vem em reais
    price_brl = (request.form.get("price_brl") or "").strip()
    price_cents = brl_to_cents(price_brl)

    if not name or not date_text:
        flash("Nome e data são obrigatórios.", "error")
        return redirect(url_for("admin_shows.shows_list"))

    # slug a partir do nome
    slug = slugify(name)

    with db() as s:
        # garante slug único (se repetir nome, adiciona sufixo)
        base = slug
        i = 2
        while s.scalar(select(Show).where(Show.slug == slug)):
            slug = f"{base}-{i}"
            i += 1

        s.add(
            Show(
                name=name,
                slug=slug,
                date_text=date_text,
                price_cents=price_cents,  # ✅ pode ser None
                is_active=1,
            )
        )

    flash("Show criado ✅", "success")
    return redirect(url_for("admin_shows.shows_list"))


@bp_admin_shows.post("/admin/shows/update/<int:show_id>")
@admin_required
def shows_update(show_id: int):
    name = (request.form.get("name") or "").strip()
    date_text = (request.form.get("date_text") or "").strip()


    # ✅ agora vem em reais
    price_brl = (request.form.get("price_brl") or "").strip()
    price_cents = brl_to_cents(price_brl)  # pode ser None

    is_active = 1 if (request.form.get("is_active") == "1") else 0

    if not name or not date_text:
        flash("Nome e data são obrigatórios.", "error")
        return redirect(url_for("admin_shows.shows_list"))

    with db() as s:
        sh = s.get(Show, show_id)
        if not sh:
            abort(404)

        sh.name = name
        sh.date_text = date_text
        sh.price_cents = price_cents
        sh.is_active = is_active
        requires_ticket = 1 if (request.form.get("requires_ticket") == "1") else 0
        sh.requires_ticket = requires_ticket


        # opcional: se você quiser atualizar slug quando muda nome (não recomendo)
        # sh.slug = slugify(name)

    flash("Show atualizado ✅", "success")
    return redirect(url_for("admin_shows.shows_list"))
