# app_services/email_templates.py
import os
from typing import List, Dict, Optional


def _unit_price_cents_from_env(default: int = 5000) -> int:
    """
    Lê TICKET_PRICE_CENTS do Render/ENV.
    default=5000 => R$ 50,00
    """
    raw = (os.getenv("TICKET_PRICE_CENTS") or "").strip()
    try:
        v = int(raw)
        return v if v > 0 else default
    except Exception:
        return default


def _fmt_brl_from_cents(cents: int) -> str:
    return f"{(cents / 100):.2f}".replace(".", ",")


def _base_url() -> str:
    return (os.getenv("BASE_URL") or "").strip().rstrip("/")


def build_tickets_email(
    *,
    buyer_name: str,
    show_name: str,
    total_brl: float,
    token: str,
    ticket_qty: int,
    pdf_all_url: str = "",
    zip_url: str = "",
    tickets: Optional[List[Dict[str, str]]] = None,  # [{name, pdf, png}]
) -> tuple[str, str, str]:
    """
    Retorna: (subject, text, html)

    - Valor unitário vem de ENV: TICKET_PRICE_CENTS (ex: 5000)
    - HTML mobile-first (botões grandes, coluna única)
    """
    unit_cents = _unit_price_cents_from_env(5000)
    unit_brl_str = _fmt_brl_from_cents(unit_cents)

    subject = f"Ingressos Sons & Sabores — {show_name}"

    # ======================
    # TEXTO (fallback)
    # ======================
    lines = []
    lines.append(f"Olá, {buyer_name}!")
    lines.append("")
    lines.append("Seus ingressos do Sons & Sabores estão prontos ✅")
    lines.append("")
    lines.append(f"Show: {show_name}")
    lines.append(f"Ingressos: {ticket_qty} × R$ {unit_brl_str}")
    lines.append(f"Total: R$ {total_brl:.2f}")
    lines.append(f"Token da compra: {token}")
    lines.append("")

    if zip_url:
        lines.append(f"Baixar todos (ZIP): {zip_url}")
    if pdf_all_url:
        lines.append(f"PDF com todos: {pdf_all_url}")

    if tickets:
        lines.append("")
        lines.append("Ingressos individuais:")
        for t in tickets:
            nm = t.get("name", "Pessoa")
            pdf = t.get("pdf", "")
            png = t.get("png", "")
            if pdf:
                lines.append(f"- {nm} (PDF): {pdf}")
            elif png:
                lines.append(f"- {nm} (PNG): {png}")

    lines.append("")
    lines.append("Apresente o QR Code dentro do ingresso na entrada.")
    lines.append("Qualquer dúvida, responda este e-mail.")
    text = "\n".join(lines)

    # ======================
    # HTML (mobile-first)
    # ======================
    def btn(url: str, label: str) -> str:
        return (
            f'<a href="{url}" target="_blank" '
            f'style="display:block;background:#111;color:#fff;text-decoration:none;'
            f'padding:14px 16px;border-radius:14px;font-size:16px;text-align:center;'
            f'margin:8px 0;">{label}</a>'
        )

    def link(url: str, label: str) -> str:
        return f'<a href="{url}" target="_blank" style="color:#111;text-decoration:underline;">{label}</a>'

    total_brl_str = f"{total_brl:.2f}".replace(".", ",")

    html = f"""
<div style="font-family: Arial, sans-serif; background:#f4f4f5; padding:16px;">
  <div style="max-width:640px; margin:0 auto; background:#ffffff; border-radius:18px; padding:18px;">
    <div style="font-size:22px; font-weight:700; margin-bottom:6px;">
      Ingressos — Sons & Sabores ✅
    </div>
    <div style="color:#666; font-size:14px; margin-bottom:16px;">
      {show_name}
    </div>

    <div style="border:1px solid #eee; border-radius:16px; padding:14px; margin-bottom:14px;">
      <div style="font-size:16px; margin-bottom:6px;"><b>Reserva de:</b> {buyer_name}</div>
      <div style="font-size:16px; margin-bottom:6px;"><b>Ingressos:</b> {ticket_qty} × R$ {unit_brl_str}</div>
      <div style="font-size:18px; margin-bottom:6px;"><b>Total:</b> R$ {total_brl_str}</div>
      <div style="color:#666; font-size:12px;"><b>Token:</b> {token}</div>
    </div>
"""

    if zip_url or pdf_all_url:
        html += """
    <div style="border:1px solid #eee; border-radius:16px; padding:14px; margin-bottom:14px;">
      <div style="font-size:16px; font-weight:700; margin-bottom:8px;">Downloads</div>
"""
        if zip_url:
            html += f"{btn(zip_url, '📦 Baixar todos (ZIP)')}"
        if pdf_all_url:
            html += f"{btn(pdf_all_url, '📄 PDF com todos')}"
        html += "    </div>\n"

    if tickets:
        html += """
    <div style="border:1px solid #eee; border-radius:16px; padding:14px; margin-bottom:14px;">
      <div style="font-size:16px; font-weight:700; margin-bottom:8px;">Ingressos individuais</div>
      <div style="font-size:14px; color:#444;">
"""
        for t in tickets:
            nm = t.get("name", "Pessoa")
            pdf = t.get("pdf", "")
            png = t.get("png", "")
            if pdf:
                html += f'<div style="margin:6px 0;">• {nm}: {link(pdf, "PDF")}</div>'
            elif png:
                html += f'<div style="margin:6px 0;">• {nm}: {link(png, "PNG")}</div>'
            else:
                html += f'<div style="margin:6px 0;">• {nm}</div>'
        html += """
      </div>
    </div>
"""

    html += """
    <div style="color:#666; font-size:12px; line-height:1.4;">
      Apresente o QR Code dentro do ingresso na entrada.<br/>
      Se precisar, responda este e-mail.
    </div>
  </div>

  <div style="max-width:640px; margin:10px auto 0; color:#999; font-size:11px; text-align:center;">
    Borogodó · Sons & Sabores
  </div>
</div>
"""
    return subject, text, html


def build_reservation_email(
    *,
    buyer_name: str,
    show_name: str,
    date_text: str,
    token: str,
    ticket_qty: int,
    guests: Optional[List[str]] = None,  # ✅ NOVO: acompanhantes
) -> tuple[str, str, str]:
    """
    E-mail de RESERVA CONFIRMADA (status: reserved).
    Inclui acompanhantes (quando houver).
    """
    subject = f"Reserva confirmada — {show_name}"

    # ======================
    # TEXTO (fallback) — OPÇÃO 3
    # ======================
    lines = [
        f"Olá, {buyer_name}!",
        "",
        "Sua reserva foi confirmada ✅",
        "",
        f"Show: {show_name}",
        f"Data: {date_text}",
        f"Pessoas: {ticket_qty}",
        f"Token: {token}",
    ]

    if guests:
        lines += ["", "Acompanhantes:"]
        for g in guests:
            lines.append(f"• {g}")

    lines += [
        "",
        "Seus ingressos (quando aplicável) serão enviados em outro e-mail/WhatsApp.",
    ]

    text = "\n".join(lines)

    # ======================
    # HTML — OPÇÃO 4
    # ======================
    guests_block = ""
    if guests:
        items = "".join(f"<li>{g}</li>" for g in guests)
        guests_block = f"""
      <div style="margin-top:12px;border:1px solid #eee;border-radius:14px;padding:14px">
        <div style="font-size:14px;margin-bottom:6px;"><b>Acompanhantes</b></div>
        <ul style="margin:0;padding-left:18px;font-size:14px;line-height:1.6;">
          {items}
        </ul>
      </div>
        """

    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:640px;margin:0 auto;padding:16px">
      <h2 style="margin:0 0 8px 0">Reserva confirmada ✅</h2>
      <div style="color:#666;margin-bottom:16px">{show_name}</div>

      <div style="border:1px solid #eee;border-radius:14px;padding:14px">
        <div><b>Comprador:</b> {buyer_name}</div>
        <div><b>Pessoas:</b> {ticket_qty}</div>
        <div><b>Data:</b> {date_text}</div>
        <div style="color:#666;font-size:12px;margin-top:8px"><b>Token:</b> {token}</div>
      </div>

      {guests_block}

      <div style="color:#666;font-size:12px;margin-top:12px">
        Ingressos (quando aplicável) serão enviados em outro e-mail/WhatsApp.
      </div>
    </div>
    """
    return subject, text, html

from typing import Optional

def build_reservation_received_email(
    *,
    buyer_name: str,
    buyer_email: str,
    show_name: str,
    token: str,
    ticket_qty: int,
    status_url: str = "",
    price_pending: bool = False,
    guests: Optional[list[str]] = None,
    unit_price_cents: Optional[int] = None,  # ✅ NOVO
) -> tuple[str, str, str]:
    """
    E-mail de RESERVA REGISTRADA (status: reservation_pending / reservation_pending_price).
    Linguagem Borogodó + HTML bonito.
    Retorna: (subject, text, html)

    unit_price_cents:
      - None: não mostra valores (a menos que você queira)
      - 0: reserva sem pagamento
      - >0: mostra "Ingressos: qty × R$ xx,xx" e Total
    """
    subject = f"Reserva registrada ✅ — {show_name}"

    if not status_url:
        base = _base_url()
        status_url = f"{base}/status/{token}" if base else ""

    guests = guests or []

    # --------- helpers BRL ----------
    def _fmt_brl_from_cents(cents: int) -> str:
        # 12345 -> "123,45"
        return f"{(cents / 100):.2f}".replace(".", ",")

    price_line_text = ""
    total_line_text = ""
    price_line_html = ""
    total_line_html = ""

    if price_pending:
        # não exibe valores
        pass
    else:
        if unit_price_cents is None:
            # se não passar unit_price_cents, não mostra valores
            pass
        elif int(unit_price_cents) <= 0:
            price_line_text = "💳 Valor: Reserva (sem pagamento)"
            price_line_html = '<div>💳 <b>Valor:</b> Reserva (sem pagamento)</div>'
        else:
            unit_str = _fmt_brl_from_cents(int(unit_price_cents))
            total_cents = int(unit_price_cents) * int(ticket_qty or 0)
            total_str = _fmt_brl_from_cents(total_cents)

            price_line_text = f"💳 Ingressos: {ticket_qty} × R$ {unit_str}"
            total_line_text = f"💰 Total: R$ {total_str}"

            price_line_html = f"<div>💳 <b>Ingressos:</b> {ticket_qty} × R$ {unit_str}</div>"
            total_line_html = f"<div>💰 <b>Total:</b> R$ {total_str}</div>"

    # =============== TEXTO (fallback) ===============
    lines = []
    lines.append(f"Oi, {buyer_name}! 😊")
    lines.append("")
    lines.append("Sua reserva no Sons & Sabores foi registrada com sucesso ✅")
    lines.append("")
    lines.append(f"🎷 Show: {show_name}")
    lines.append(f"👥 Pessoas: {ticket_qty}")

    if price_line_text:
        lines.append(price_line_text)
    if total_line_text:
        lines.append(total_line_text)

    lines.append(f"🔎 Token da reserva: {token}")

    if guests:
        lines.append("")
        lines.append("Acompanhantes:")
        for g in guests:
            lines.append(f"• {g}")

    lines.append("")

    if price_pending:
        lines.append("⚠️ Este show ainda está com o preço em definição.")
        lines.append("Assim que definirmos, a gente te manda as instruções por e-mail/WhatsApp.")
        lines.append("")
    else:
        lines.append("Agora é com a gente:")
        lines.append("• Vamos confirmar sua reserva e te avisar por WhatsApp ou e-mail em até 3 dias.")
        lines.append("")

    if status_url:
        lines.append("Se quiser acompanhar o status:")
        lines.append(status_url)
        lines.append("")

    lines.append("Qualquer dúvida, responde este e-mail ou chama a gente no WhatsApp.")
    lines.append("")
    lines.append("Com carinho,")
    lines.append("Borogodó · Sons & Sabores")
    text = "\n".join(lines)

    # =============== HTML ===============
    optional_price_block = ""
    if price_pending:
        optional_price_block = """
          <div style="margin-top:12px;border:1px solid #fde68a;background:#fffbeb;border-radius:14px;padding:12px 14px;color:#92400e;">
            ⚠️ <b>Preço em definição</b><br/>
            Este show ainda está com o preço em definição. Assim que definirmos, a gente te manda as instruções por e-mail/WhatsApp.
          </div>
        """

    button_block = ""
    if status_url:
        button_block = f"""
          <div style="margin-top:14px;padding:12px 14px;border-radius:14px;background:#111827;">
            <a href="{status_url}" style="display:inline-block;color:#ffffff;text-decoration:none;font-weight:700;">
              Acompanhar status da reserva →
            </a>
          </div>
        """

    guests_block = ""
    if guests:
        items = "".join(f"<li>{g}</li>" for g in guests)
        guests_block = f"""
          <div style="margin-top:12px;border:1px solid #e5e7eb;border-radius:14px;padding:12px 14px;">
            <div style="font-size:13px;color:#6b7280;margin-bottom:6px;">Acompanhantes</div>
            <ul style="margin:0;padding-left:18px;color:#111827;font-size:14px;line-height:1.6;">
              {items}
            </ul>
          </div>
        """

    price_block_html = ""
    if (price_line_html or total_line_html) and (not price_pending):
        price_block_html = f"""
          <div style="margin-top:12px;border:1px solid #e5e7eb;border-radius:14px;padding:12px 14px;background:#fafafa;">
            <div style="font-size:13px;color:#6b7280;margin-bottom:6px;">Valores</div>
            <div style="font-size:14px;color:#111827;line-height:1.6;">
              {price_line_html}
              {total_line_html}
            </div>
          </div>
        """

    html = f"""<!doctype html>
<html lang="pt-BR">
  <body style="margin:0;padding:0;background:#f4f4f5;font-family:Arial,Helvetica,sans-serif;">
    <div style="max-width:640px;margin:0 auto;padding:24px;">
      <div style="background:#ffffff;border-radius:16px;box-shadow:0 6px 18px rgba(0,0,0,.06);overflow:hidden;">
        <div style="padding:22px 22px 10px 22px;">
          <div style="font-size:14px;color:#71717a;">Borogodó · Sons & Sabores</div>
          <h1 style="margin:10px 0 6px 0;font-size:22px;line-height:1.2;color:#111827;">
            Reserva registrada ✅
          </h1>
          <p style="margin:0;color:#374151;font-size:15px;line-height:1.6;">
            Oi, <b>{buyer_name}</b>! 😊<br/>
            Sua reserva foi registrada com sucesso. Agora é com a gente!
          </p>
        </div>

        <div style="padding:0 22px 18px 22px;">
          <div style="border:1px solid #e5e7eb;border-radius:14px;padding:14px 14px;background:#fafafa;">
            <div style="font-size:13px;color:#6b7280;margin-bottom:10px;">Detalhes</div>
            <div style="font-size:15px;color:#111827;line-height:1.6;">
              <div>🎷 <b>Show:</b> {show_name}</div>
              <div>👥 <b>Pessoas:</b> {ticket_qty}</div>
              <div>🔎 <b>Token da reserva:</b>
                <span style="font-family:ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace;">{token}</span>
              </div>
            </div>
          </div>

          {price_block_html}
          {guests_block}
          {optional_price_block}

          <div style="margin-top:14px;font-size:14px;color:#374151;line-height:1.7;">
            <b>Próximos passos</b><br/>
            {"• Vamos confirmar sua reserva e te avisar por WhatsApp ou e-mail em até <b>3 dias</b>." if not price_pending else "• Assim que o preço estiver definido, a gente te manda as instruções por e-mail/WhatsApp."}
          </div>

          {button_block}

          <p style="margin:16px 0 0 0;font-size:13px;color:#6b7280;line-height:1.6;">
            Se precisar de algo, responde este e-mail ou chama a gente no WhatsApp.
          </p>
        </div>

        <div style="padding:14px 22px;background:#fafafa;border-top:1px solid #e5e7eb;">
          <div style="font-size:12px;color:#6b7280;">
            Com carinho, <b>Borogodó</b> 💛
          </div>
        </div>
      </div>

      <div style="text-align:center;margin-top:14px;font-size:11px;color:#9ca3af;">
        Você recebeu este e-mail porque uma reserva foi feita no nosso site.
      </div>
    </div>
  </body>
</html>"""

    return subject, text, html
