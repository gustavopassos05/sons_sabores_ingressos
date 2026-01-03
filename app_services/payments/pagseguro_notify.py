# app_services/payments/pagseguro_notify.py
import os
import xml.etree.ElementTree as ET
from typing import Dict, Any

import requests


def _env() -> str:
    return (os.getenv("PAGSEGURO_ENV", "sandbox") or "sandbox").lower().strip()


def _base_ws() -> str:
    # PagSeguro Classic WS
    return "https://ws.sandbox.pagseguro.uol.com.br" if _env() == "sandbox" else "https://ws.pagseguro.uol.com.br"


def _credentials() -> tuple[str, str]:
    email = (os.getenv("PAGSEGURO_EMAIL") or "").strip()
    token = (os.getenv("PAGSEGURO_TOKEN") or "").strip()
    if not email or not token:
        raise RuntimeError("PAGSEGURO_EMAIL e PAGSEGURO_TOKEN precisam estar nas env vars do Render.")
    return email, token


def fetch_transaction_by_notification(notification_code: str) -> Dict[str, Any]:
    """
    Retorna dados principais da transação:
      {
        "code": "...",         # transaction code
        "reference": "...",    # ex: purchase-123
        "status": 3,           # int
        "raw_xml": "<...>"
      }

    Status (PagSeguro Classic):
      1 aguardando pagamento
      2 em análise
      3 paga
      4 disponível
      5 em disputa
      6 devolvida
      7 cancelada
    """
    email, token = _credentials()

    url = f"{_base_ws()}/v3/transactions/notifications/{notification_code}"
    r = requests.get(url, params={"email": email, "token": token}, timeout=30)
    if not r.ok:
        raise RuntimeError(f"PagSeguro notify erro {r.status_code}: {r.text}")

    xml_text = (r.text or "").strip()
    root = ET.fromstring(xml_text)

    code = (root.findtext("code") or "").strip()
    reference = (root.findtext("reference") or "").strip()
    status_txt = (root.findtext("status") or "").strip()

    try:
        status = int(status_txt)
    except Exception:
        status = 0

    return {"code": code, "reference": reference, "status": status, "raw_xml": xml_text}
