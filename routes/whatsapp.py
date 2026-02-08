# routes/whatsapp.py
import os
from flask import Blueprint, redirect, abort, current_app
from sqlalchemy import select

from db import db
from models import Purchase  # ✅ aqui é o modelo certo do projeto de ingressos

from app_services.utils_whatsapp import (
    whatsapp_link,
    phone_to_e164_br,
)

bp_whats = Blueprint("whats", __name__)

BOROGODO_WHATS = (os.getenv("BOROGODO_WHATS") or "5531999613652").strip()


def _get_purchase_or_404(token: str) -> Purchase:
    token = (token or "").strip()
    if not token:
        abort(404)

    with db() as s:
        p = s.scalar(select(Purchase).where(Purchase.token == token))
        if not p:
            abort(404)
        s.expunge(p)  # descola da sessão
        return p


def _msg_para_borogodo(p: Purchase) -> str:
    guests = (p.guests_text or "").strip()
    guests_block = f"\nConvidados:\n{guests}\n" if guests else ""

    return (
        "🍽️ *Nova solicitação — Sons & Sabores*\n"
        f"Show: {p.show_name}\n"
        f"Nome: {p.buyer_name}\n"
        f"CPF: {p.buyer_cpf_digits or p.buyer_cpf}\n"
        f"E-mail: {p.buyer_email or '-'}\n"
        f"Telefone: {p.buyer_phone or '-'}\n"
        f"Pessoas: {p.ticket_qty or 1}\n"
        f"Status: {p.status}\n"
        f"Token: {p.token}\n"
        f"{guests_block}"
        "\n✅ Conferir no painel e confirmar com o cliente."
    )


def _msg_para_cliente(p: Purchase) -> str:
    return (
        "🍽️ *Borogodó — Solicitação recebida ✅*\n"
        f"Olá, {p.buyer_name}! Recebemos sua solicitação:\n\n"
        f"🎷 Show: {p.show_name}\n"
        f"👥 Pessoas: {p.ticket_qty or 1}\n"
        f"🔎 Código: {p.token}\n\n"
        "Assim que confirmarmos, te avisamos por aqui. 😊"
    )


@bp_whats.get("/whats/purchase/<token>/borogodo")
def whats_borogodo_purchase(token: str):
    p = _get_purchase_or_404(token)
    link = whatsapp_link(BOROGODO_WHATS, _msg_para_borogodo(p))
    return redirect(link)


@bp_whats.get("/whats/purchase/<token>/cliente")
def whats_cliente_purchase(token: str):
    p = _get_purchase_or_404(token)

    tel = (p.buyer_phone or "").strip()
    if not tel:
        current_app.logger.warning("[WHATS] Purchase %s sem buyer_phone", p.token)
        abort(400, description="Compra sem telefone do cliente.")

    link = whatsapp_link(tel, _msg_para_cliente(p))
    return redirect(link)


# ====== BÔNUS (disparo "automático" via link pronto) ======
# WhatsApp normal não permite envio automático servidor->cliente sem API oficial.
# O que dá pra fazer é: gerar links prontos, salvar no banco/log, e abrir com 1 clique.
def build_whats_links_for_purchase(p: Purchase) -> dict:
    cliente_phone = (p.buyer_phone or "").strip()
    return {
        "borogodo_phone": phone_to_e164_br(BOROGODO_WHATS),
        "cliente_phone": phone_to_e164_br(cliente_phone) if cliente_phone else "",
        "link_borogodo": whatsapp_link(BOROGODO_WHATS, _msg_para_borogodo(p)),
        "link_cliente": whatsapp_link(cliente_phone, _msg_para_cliente(p)) if cliente_phone else "",
    }
