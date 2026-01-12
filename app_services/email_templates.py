# app_services/email_templates.py
from typing import List, Dict, Optional
from datetime import datetime

def build_tickets_email(
    *,
    buyer_name: str,
    show_name: str,
    total_brl: float,
    token: str,
    pdf_all_url: str = "",
    zip_url: str = "",
    tickets: Optional[List[Dict[str, str]]] = None,  # [{name, pdf, png}]
) -> tuple[str, str, str]:
    """
    Retorna: (subject, text, html)
    """
    subject = f"Ingressos Sons & Sabores — {show_name}"

    lines = []
    lines.append(f"Olá, {buyer_name}!")
    lines.append("")
    lines.append("Seus ingressos do Sons & Sabores estão prontos ✅")
    lines.append("")
    lines.append(f"Show: {show_name}")
    lines.append(f"Token da compra: {token}")
    lines.append(f"Total: R$ {total_brl:.2f}")
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

    # HTML simples e bonito
    def _a(url: str, label: str) -> str:
        return f'<a href="{url}" target="_blank" style="color:#111;text-decoration:underline">{label}</a>'

    html_parts = []
    html_parts.append('<div style="font-family:Arial,sans-serif;max-width:640px;margin:0 auto;padding:16px">')
    html_parts.append('<h2 style="margin:0 0 6px 0">Ingressos — Sons & Sabores ✅</h2>')
    html_parts.append(f'<div style="color:#666;font-size:14px;margin-bottom:16px">{show_name}</div>')

    html_parts.append('<div style="border:1px solid #eee;border-radius:14px;padding:14px;margin-bottom:14px">')
    html_parts.append(f'<div><b>Comprador:</b> {buyer_name}</div>')
    html_parts.append(f'<div><b>Total:</b> R$ {total_brl:.2f}</div>')
    html_parts.append(f'<div style="color:#666;font-size:12px;margin-top:6px"><b>Token:</b> {token}</div>')
    html_parts.append('</div>')

    if zip_url or pdf_all_url:
        html_parts.append('<div style="border:1px solid #eee;border-radius:14px;padding:14px;margin-bottom:14px">')
        html_parts.append('<div style="margin-bottom:8px"><b>Downloads</b></div>')
        if zip_url:
            html_parts.append(f'<div>📦 {_a(zip_url, "Baixar todos (ZIP)")}</div>')
        if pdf_all_url:
            html_parts.append(f'<div>📄 {_a(pdf_all_url, "PDF com todos")}</div>')
        html_parts.append('</div>')

    if tickets:
        html_parts.append('<div style="border:1px solid #eee;border-radius:14px;padding:14px;margin-bottom:14px">')
        html_parts.append('<div style="margin-bottom:8px"><b>Ingressos individuais</b></div>')
        html_parts.append('<ul style="margin:0;padding-left:18px">')
        for t in tickets:
            nm = t.get("name", "Pessoa")
            pdf = t.get("pdf", "")
            png = t.get("png", "")
            if pdf:
                html_parts.append(f'<li>{nm}: {_a(pdf, "PDF")}</li>')
            elif png:
                html_parts.append(f'<li>{nm}: {_a(png, "PNG")}</li>')
            else:
                html_parts.append(f'<li>{nm}</li>')
        html_parts.append('</ul>')
        html_parts.append('</div>')

    html_parts.append('<div style="color:#666;font-size:12px">Apresente o QR Code dentro do ingresso na entrada.</div>')
    html_parts.append('</div>')
    html = "".join(html_parts)

    return subject, text, html
