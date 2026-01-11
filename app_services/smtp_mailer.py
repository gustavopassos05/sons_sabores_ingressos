# app_services/smtp_mailer.py
import os
import ssl
import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import Optional


def smtp_cfg():
    return {
        "host": (os.getenv("SMTP_HOST") or "").strip(),
        "port": int(os.getenv("SMTP_PORT", "587")),
        "user": (os.getenv("SMTP_USERNAME") or "").strip(),
        "password": (os.getenv("SMTP_PASSWORD") or "").strip(),
        "from_addr": (os.getenv("SMTP_FROM") or "").strip(),
        "from_name": (os.getenv("SMTP_FROM_NAME") or "Borogodó · Ingressos").strip(),
        "tls": (os.getenv("SMTP_TLS", "1") != "0"),  # 1: STARTTLS (587) / 0: SSL (465) ou SMTP puro
    }


def send_email(
    *,
    to_email: str,
    subject: str,
    body: str,
    attachment_path: Optional[str | Path] = None,
    attachment_mime: Optional[str] = None,
) -> None:
    cfg = smtp_cfg()
    if not cfg["host"] or not cfg["user"] or not cfg["password"] or not cfg["from_addr"]:
        raise RuntimeError("SMTP_* não configurado no Render (HOST/USERNAME/PASSWORD/FROM).")

    msg = EmailMessage()
    msg["From"] = f'{cfg["from_name"]} <{cfg["from_addr"]}>'
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)

    if attachment_path:
        p = Path(attachment_path)
        data = p.read_bytes()
        mime = (attachment_mime or "application/octet-stream").split("/", 1)
        maintype = mime[0] if len(mime) > 0 else "application"
        subtype = mime[1] if len(mime) > 1 else "octet-stream"
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=p.name)

    # 587 STARTTLS
    if cfg["tls"] and cfg["port"] == 587:
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(cfg["user"], cfg["password"])
            smtp.send_message(msg)
        return

    # 465 SSL (se SMTP_TLS=0 e port=465)
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
