# app_services/email_service.py
import os
import ssl
import smtplib
from typing import Optional, Dict, List, Any
from email.message import EmailMessage


def _cfg() -> Dict[str, object]:
    return {
        "host": (os.getenv("SMTP_HOST") or "").strip(),
        "port": int(os.getenv("SMTP_PORT", "587")),
        "user": (os.getenv("SMTP_USERNAME") or "").strip(),
        "password": (os.getenv("SMTP_PASSWORD") or "").strip(),
        "from_addr": (os.getenv("SMTP_FROM") or "").strip(),
        "from_name": (os.getenv("SMTP_FROM_NAME") or "Borogodó · Sons & Sabores").strip(),
        "tls": (os.getenv("SMTP_TLS", "1").strip() != "0"),
    }


def send_email(
    *,
    to_email: str,
    subject: str,
    body_text: str,
    body_html: Optional[str] = None,
    reply_to: Optional[str] = None,
    attachments: Optional[List[Dict[str, Any]]] = None,  # ✅ NOVO: anexos
) -> None:
    """
    Envia e-mail via SMTP com suporte a anexos.

    attachments: lista de dicts no formato:
      {
        "filename": "comprovante.pdf",
        "content_type": "application/pdf",
        "data": b"...bytes..."
      }
    """
    cfg = _cfg()
    if not cfg["host"] or not cfg["user"] or not cfg["password"] or not cfg["from_addr"]:
        raise RuntimeError("Configure SMTP_HOST/SMTP_PORT/SMTP_USERNAME/SMTP_PASSWORD/SMTP_FROM no Render.")

    msg = EmailMessage()
    msg["From"] = f'{cfg["from_name"]} <{cfg["from_addr"]}>'
    msg["To"] = to_email
    msg["Subject"] = subject
    if reply_to:
        msg["Reply-To"] = reply_to

    msg.set_content(body_text or "")

    # HTML opcional
    if body_html:
        msg.add_alternative(body_html, subtype="html")

    # ✅ Anexos
    for att in attachments or []:
        filename = (att.get("filename") or "anexo").strip()
        data = att.get("data") or b""
        content_type = (att.get("content_type") or "application/octet-stream").strip().lower()

        if not data:
            continue

        if "/" in content_type:
            maintype, subtype = content_type.split("/", 1)
        else:
            maintype, subtype = "application", "octet-stream"

        msg.add_attachment(
            data,
            maintype=maintype,
            subtype=subtype,
            filename=filename,
        )

    # 587 STARTTLS
    if cfg["tls"] and cfg["port"] == 587:
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(cfg["user"], cfg["password"])
            smtp.send_message(msg)
        return

    # 465 SSL direto (se SMTP_TLS=0 e porta 465)
    if (not cfg["tls"]) and cfg["port"] == 465:
        with smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=30, context=ssl.create_default_context()) as smtp:
            smtp.login(cfg["user"], cfg["password"])
            smtp.send_message(msg)
        return

    # fallback
    with smtplib.SMTP(cfg["host"], cfg["port"], timeout=30) as smtp:
        smtp.ehlo()
        if cfg["tls"]:
            smtp.starttls(context=ssl.create_default_context())
        smtp.login(cfg["user"], cfg["password"])
        smtp.send_message(msg)
