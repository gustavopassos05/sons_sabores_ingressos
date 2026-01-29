# routes/admin_shows.py
import os
import re
import unicodedata
from pathlib import Path

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, current_app
from sqlalchemy import select, desc
from werkzeug.utils import secure_filename

from db import db
from models import Show
from routes.admin_auth import admin_required
from app_services.ftp_uploader import upload_file  # ✅ precisa existir

bp_admin_shows = Blueprint("admin_shows", __name__)


# -----------------------------
# Helpers
# -----------------------------
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


def _allowed_image_ext(filename: str) -> str | None:
    fn = (filename or "").lower().strip()
    if "." not in fn:
        return None
    ext = fn.rsplit(".", 1)[-1]
    if ext in {"jpg", "jpeg", "png", "webp"}:
        return ext
    return None


def _get_ftp_public_base() -> str:
    public_base = (os.getenv("FTP_PUBLIC_BASE") or "").strip().rstrip("/")
    if not public_base:
        raise RuntimeError("FTP_PUBLIC_BASE não configurado (URL pública dos arquivos).")
    if not public_base.startswith("http"):
        raise RuntimeError(f"FTP_PUBLIC_BASE inválido (precisa começar com http/https): {public_base}")
    return public_base


def _save_show_image_to_ftp(*, file_storage, show_slug: str) -> str:
    """
    Recebe um FileStorage do Flask, salva em /tmp e faz upload via FTP.
    Retorna URL pública final.
    """
    ext = _allowed_image_ext(file_storage.filename)
    if not ext:
        raise ValueError("Formato inválido. Use JPG, PNG ou WEBP.")

    tmp_dir = Path("/tmp/shows").resolve()
    tmp_dir.mkdir(parents=True, exist_ok=True)

    safe = secure_filename(file_storage.filename) or f"{show_slug}.{ext}"
    # Nome remoto padronizado (evita colisão e não usa pastas)
    remote_name = f"show-{show_slug}.{ext}"

    tmp_path = tmp_dir / f"{show_slug}-{safe}"
    file_storage.save(tmp_path)

    ok, info = upload_file(tmp_path, remote_name)
    try:
        tmp_path.unlink(missing_ok=True)
    except Exception:
        pass

    if not ok:
        raise RuntimeError(str(info))

    public_base = _get_ftp_public_base()
    return f"{public_base}/{remote_name}"


# -----------------------------
# Views
# -----------------------------
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
    title = (request.form.get("title") or "").strip()
    description = (request.form.get("description") or "").strip()
    date_text = (request.form.get("date_text") or "").strip()
    capacity_raw = (request.form.get("capacity") or "").strip()
    capacity = int(capacity_raw) if capacity_raw.isdigit() else None


    price_brl = (request.form.get("price_brl") or "").strip()
    price_cents = brl_to_cents(price_brl)

    requires_ticket = 1 if (request.form.get("requires_ticket") == "1") else 0

    if not name or not date_text:
        flash("Nome e data são obrigatórios.", "error")
        return redirect(url_for("admin_shows.shows_list"))

    # slug a partir do nome
    slug = slugify(name)

    image_file = request.files.get("image_file")  # ✅ <input type="file" name="image_file">

    with db() as s:
        # garante slug único
        base = slug
        i = 2
        while s.scalar(select(Show).where(Show.slug == slug)):
            slug = f"{base}-{i}"
            i += 1

        sh = Show(
            name=name,
            slug=slug,
            date_text=date_text,
            price_cents=price_cents,  # pode ser None
            is_active=1,
            requires_ticket=requires_ticket,
            capacity=capacity

        )

        # campos novos (no model)
        if hasattr(sh, "title"):
            sh.title = title or name
        if hasattr(sh, "description"):
            sh.description = description or ""

        # upload imagem (opcional)
        if image_file and image_file.filename:
            try:
                img_url = _save_show_image_to_ftp(file_storage=image_file, show_slug=slug)
                if hasattr(sh, "image_url"):
                    sh.image_url = img_url
            except Exception as e:
                current_app.logger.warning("[SHOW IMAGE] upload falhou: %s", e)
                flash(f"Show criado, mas a imagem não subiu: {e}", "error")

        s.add(sh)
        s.commit()

    flash("Show criado ✅", "success")
    return redirect(url_for("admin_shows.shows_list"))


@bp_admin_shows.post("/admin/shows/update/<int:show_id>")
@admin_required
def shows_update(show_id: int):
    name = (request.form.get("name") or "").strip()
    title = (request.form.get("title") or "").strip()
    description = (request.form.get("description") or "").strip()
    date_text = (request.form.get("date_text") or "").strip()

    price_brl = (request.form.get("price_brl") or "").strip()
    price_cents = brl_to_cents(price_brl)  # pode ser None
    capacity_raw = (request.form.get("capacity") or "").strip()
    capacity = int(capacity_raw) if capacity_raw.isdigit() else None

    is_active = 1 if (request.form.get("is_active") == "1") else 0
    requires_ticket = 1 if (request.form.get("requires_ticket") == "1") else 0

    if not name or not date_text:
        flash("Nome e data são obrigatórios.", "error")
        return redirect(url_for("admin_shows.shows_list"))

    image_file = request.files.get("image_file")  # ✅ opcional

    with db() as s:
        sh = s.get(Show, show_id)
        if not sh:
            abort(404)

        sh.name = name
        sh.date_text = date_text
        sh.price_cents = price_cents
        sh.is_active = is_active
        sh.requires_ticket = requires_ticket
        sh.capacity = capacity

        # campos novos (no model)
        if hasattr(sh, "title"):
            sh.title = title or name
        if hasattr(sh, "description"):
            sh.description = description or ""

        # (recomendação) não mudar slug automaticamente
        # sh.slug = slugify(name)

        # upload imagem (opcional)
        if image_file and image_file.filename:
            try:
                img_url = _save_show_image_to_ftp(file_storage=image_file, show_slug=sh.slug)
                if hasattr(sh, "image_url"):
                    sh.image_url = img_url
            except Exception as e:
                current_app.logger.warning("[SHOW IMAGE] upload falhou: %s", e)
                flash(f"Show atualizado, mas a imagem não subiu: {e}", "error")

        s.add(sh)
        s.commit()

    flash("Show atualizado ✅", "success")
    return redirect(url_for("admin_shows.shows_list"))
