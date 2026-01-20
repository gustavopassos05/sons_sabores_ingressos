# routes/admin_shows.py
import re, unicodedata
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from sqlalchemy import select, desc

from db import db
from models import Show
from routes.admin_auth import admin_required

bp_admin_shows = Blueprint("admin_shows", __name__)

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
    price_raw = (request.form.get("price_cents") or "").strip()

    if not name or not date_text:
        flash("Nome e data são obrigatórios.", "error")
        return redirect(url_for("admin_shows.shows_list"))

    price_cents = None
    if price_raw:
        try:
            price_cents = int(price_raw)
        except Exception:
            flash("Preço inválido. Use centavos (ex: 5000).", "error")
            return redirect(url_for("admin_shows.shows_list"))

    slug = slugify(name)

    with db() as s:
        # garante slug único (se repetir nome, adiciona sufixo)
        base = slug
        i = 2
        while s.scalar(select(Show).where(Show.slug == slug)):
            slug = f"{base}-{i}"
            i += 1

        s.add(Show(name=name, slug=slug, date_text=date_text, price_cents=price_cents, is_active=1))

    flash("Show criado ✅", "success")
    return redirect(url_for("admin_shows.shows_list"))

@bp_admin_shows.post("/admin/shows/update/<int:show_id>")
@admin_required
def shows_update(show_id: int):
    name = (request.form.get("name") or "").strip()
    date_text = (request.form.get("date_text") or "").strip()
    price_raw = (request.form.get("price_cents") or "").strip()
    is_active = 1 if (request.form.get("is_active") == "1") else 0

    if not name or not date_text:
        flash("Nome e data são obrigatórios.", "error")
        return redirect(url_for("admin_shows.shows_list"))

    price_cents = None
    if price_raw:
        try:
            price_cents = int(price_raw)
        except Exception:
            flash("Preço inválido. Use centavos (ex: 5000).", "error")
            return redirect(url_for("admin_shows.shows_list"))

    with db() as s:
        sh = s.get(Show, show_id)
        if not sh:
            abort(404)

        sh.name = name
        sh.date_text = date_text
        sh.price_cents = price_cents
        sh.is_active = is_active

    flash("Show atualizado ✅", "success")
    return redirect(url_for("admin_shows.shows_list"))
