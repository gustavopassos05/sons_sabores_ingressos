# routes/whatsapp.py
import os
from flask import Blueprint, redirect, abort, current_app
from flask_login import login_required
from sqlalchemy import select

from db import db  # ✅ igual no app: from db import engine, db
from models import Reserva

from app_services.utils_whatsapp import (
    whatsapp_link,
    msg_para_borogodo,
    msg_para_cliente,
    phone_to_e164_br,
)

bp_whats = Blueprint("whats", __name__)

BOROGODO_WHATS = os.getenv("BOROGODO_WHATS", "5531999613652").strip()


def _get_reserva_or_404(reserva_id: int) -> Reserva:
    with db() as s:
        r = s.scalar(select(Reserva).where(Reserva.id == reserva_id))
        if not r:
            abort(404)
        # destaca da sessão pra evitar lazy-load depois
        s.expunge(r)
        return r


@bp_whats.get("/reserva/<int:reserva_id>/whats/borogodo")
@login_required
def whats_borogodo(reserva_id: int):
    r = _get_reserva_or_404(reserva_id)
    link = whatsapp_link(BOROGODO_WHATS, msg_para_borogodo(r))
    return redirect(link)


@bp_whats.get("/reserva/<int:reserva_id>/whats/cliente")
@login_required
def whats_cliente(reserva_id: int):
    r = _get_reserva_or_404(reserva_id)

    tel = (getattr(r, "telefone", "") or "").strip()
    if not tel:
        current_app.logger.warning("[WHATS] Reserva %s sem telefone do cliente", reserva_id)
        abort(400, description="Reserva sem telefone do cliente.")

    link = whatsapp_link(tel, msg_para_cliente(r))
    return redirect(link)


# ==========================
# ✅ “DISPARO AUTOMÁTICO” (BÔNUS)
# ==========================
# WhatsApp comum NÃO permite enviar automático via servidor sem API oficial.
# Então o “automático” aqui é: gerar links prontos e você decide abrir / salvar / mostrar no painel.

def build_whats_links_for_reserva(reserva) -> dict:
    cliente_phone = (getattr(reserva, "telefone", "") or "").strip()
    return {
        "borogodo_phone": phone_to_e164_br(BOROGODO_WHATS),
        "cliente_phone": phone_to_e164_br(cliente_phone) if cliente_phone else "",
        "link_borogodo": whatsapp_link(BOROGODO_WHATS, msg_para_borogodo(reserva)),
        "link_cliente": whatsapp_link(cliente_phone, msg_para_cliente(reserva)) if cliente_phone else "",
    }


def auto_whatsapp_on_new_reserva(reserva) -> dict:
    links = build_whats_links_for_reserva(reserva)
    current_app.logger.info(
        "[WHATS/AUTO] reserva_id=%s link_borogodo=%s link_cliente=%s",
        getattr(reserva, "id", None),
        links.get("link_borogodo"),
        links.get("link_cliente"),
    )
    return links
