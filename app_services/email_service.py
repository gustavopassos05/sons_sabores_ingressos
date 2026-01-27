import os
import ssl
import smtplib
import threading
from typing import Optional, Dict, List, Any
from email.message import EmailMessage
from datetime import datetime


def _cfg() -> Dict[str, object]:
    return {
        "host": (os.getenv("SMTP_HOST") or "").strip(),
        "port": int(os.getenv("SMTP_PORT", "587")),
        "user": (os.getenv("SMTP_USERNAME") or "").strip(),
        "password": (os.getenv("SMTP_PASSWORD") or "").strip(),
        "from_addr": (os.getenv("SMTP_FROM") or "").strip(),
        "from_name": (os.getenv("SMTP_FROM_NAME") or "Borogodó · Sons & Sabores").strip(),
        "tls": (os.getenv("SMTP_TLS", "1").strip() != "0"),
        "timeout": int(os.getenv("SMTP_TIMEOUT", "30")),
        "debug": (os.getenv("EMAIL_DEBUG", "0").strip() == "1"),
    }


def send_email(
    *,
    to_email: str,
    subject: str,
    body_text: str,
    body_html: Optional[str] = None,
    reply_to: Optional[str] = None,
    attachments: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """
    Envia e-mail via SMTP com suporte a anexos.
    Retorna o Message-ID (string) para rastreamento.

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

    # ajuda a rastrear no log/provedor
    msg["X-App"] = "SonsSabores"
    msg["X-Sent-At"] = datetime.utcnow().isoformat() + "Z"

    msg.set_content(body_text or "")

    if body_html:
        msg.add_alternative(body_html, subtype="html")

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

        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)

    timeout = int(cfg["timeout"])
    debug = bool(cfg["debug"])

    def _smtp_send(smtp_obj):
        if debug:
            smtp_obj.set_debuglevel(1)  # imprime conversa SMTP nos logs
        smtp_obj.send_message(msg)

    # 587 STARTTLS
    if cfg["tls"] and cfg["port"] == 587:
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=timeout) as smtp:
            smtp.ehlo()
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(cfg["user"], cfg["password"])
            _smtp_send(smtp)
        return str(msg.get("Message-ID") or "")

    # 465 SSL direto (se SMTP_TLS=0 e porta 465)
    if (not cfg["tls"]) and cfg["port"] == 465:
        with smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=timeout, context=ssl.create_default_context()) as smtp:
            smtp.login(cfg["user"], cfg["password"])
            _smtp_send(smtp)
        return str(msg.get("Message-ID") or "")

    # fallback
    with smtplib.SMTP(cfg["host"], cfg["port"], timeout=timeout) as smtp:
        smtp.ehlo()
        if cfg["tls"]:
            smtp.starttls(context=ssl.create_default_context())
        smtp.login(cfg["user"], cfg["password"])
        _smtp_send(smtp)

    return str(msg.get("Message-ID") or "")


def send_email_async(**kwargs) -> None:
    """
    Dispara send_email em uma thread (não bloqueia o request).
    Uso:
      send_email_async(to_email=..., subject=..., body_text=..., body_html=...)
    """
    def runner():
        try:
            send_email(**kwargs)
        except Exception:
            # não explode o request; o caller deve logar se quiser
            pass

    t = threading.Thread(target=runner, daemon=True)
    t.start()
