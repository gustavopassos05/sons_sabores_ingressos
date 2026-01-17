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
    # helpers inline-friendly
    def btn(url: str, label: str) -> str:
        # botão grande (tap-friendly)
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
      <div style="font-size:16px; margin-bottom:6px;"><b>Comprador:</b> {buyer_name}</div>
      <div style="font-size:16px; margin-bottom:6px;"><b>Ingressos:</b> {ticket_qty} × R$ {unit_brl_str}</div>
      <div style="font-size:18px; margin-bottom:6px;"><b>Total:</b> R$ {total_brl_str}</div>
      <div style="color:#666; font-size:12px;"><b>Token:</b> {token}</div>
    </div>
"""

    # Downloads com botões (melhor no celular)
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

    # Lista individual (em mobile, link simples — sem poluir)
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
