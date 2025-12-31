# services/ftp_uploader.py
import os
from ftplib import FTP, FTP_TLS
from pathlib import Path
from typing import Tuple, Dict, Any


def _cfg():
    hosts = [h.strip() for h in (os.getenv("FTP_HOSTS") or "").split(",") if h.strip()]
    if not hosts:
        raise RuntimeError("FTP_HOSTS não configurado no .env")

    return {
        "hosts": hosts,
        "port": int(os.getenv("FTP_PORT", "21")),
        "passive": (os.getenv("FTP_PASSIVE", "1") != "0"),
        "security": (os.getenv("FTP_SECURITY", "explicit") or "explicit").lower().strip(),  # explicit|none
        "username": (os.getenv("FTP_USERNAME") or "").strip(),
        "password": (os.getenv("FTP_PASSWORD") or "").strip(),
        "dir": (os.getenv("FTP_DIR") or "").strip(),
        "public_base": (os.getenv("FTP_PUBLIC_BASE") or "").rstrip("/"),
    }


def _ftp_connect(host: str, port: int, security: str, passive: bool, username: str, password: str):
    if security == "explicit":
        ftp = FTP_TLS()
        ftp.connect(host, port, timeout=25)
        ftp.auth()
        ftp.login(username, password)
        ftp.prot_p()
    elif security == "none":
        ftp = FTP()
        ftp.connect(host, port, timeout=25)
        ftp.login(username, password)
    else:
        raise ValueError(f"FTP_SECURITY inválido: {security}")

    ftp.set_pasv(bool(passive))
    ftp.encoding = "utf-8"
    return ftp


def _ensure_remote_dir(ftp, remote_dir: str):
    # aceita /public_html/... e cria se precisar
    path = (remote_dir or "").strip()
    if not path:
        return

    if path.startswith("/"):
        try:
            ftp.cwd("/")
        except Exception:
            pass
        path = path.strip("/")

    for part in [p for p in path.split("/") if p]:
        try:
            ftp.cwd(part)
        except Exception:
            ftp.mkd(part)
            ftp.cwd(part)


def upload_file(local_path: str | Path, remote_filename: str) -> Tuple[bool, Dict[str, Any] | str]:
    cfg = _cfg()
    local_path = str(local_path)

    for host in cfg["hosts"]:
        try:
            ftp = _ftp_connect(
                host=host,
                port=cfg["port"],
                security=cfg["security"],
                passive=cfg["passive"],
                username=cfg["username"],
                password=cfg["password"],
            )
            with ftp:
                _ensure_remote_dir(ftp, cfg["dir"])
                with open(local_path, "rb") as f:
                    ftp.storbinary(f"STOR {remote_filename}", f)

            public_url = f"{cfg['public_base']}/{remote_filename}" if cfg["public_base"] else ""
            return True, {"host": host, "file": remote_filename, "public_url": public_url}

        except Exception as e:
            last = f"[FTP ERRO] host={host} -> {e}"

    return False, last
