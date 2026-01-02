# routes/ftp.py
from flask import Blueprint, redirect, url_for, flash, abort
from sqlalchemy import select

from db import db
from models import Purchase, Ticket
from app_services.ftp_uploader import upload_file  # ✅ caminho correto

bp_ftp = Blueprint("ftp", __name__)


@bp_ftp.post("/admin/ftp/push/<purchase_token>")
def ftp_push_purchase(purchase_token: str):
    # TODO: aqui você pluga sua checagem de admin (require_login)
    with db() as s:
        purchase = s.scalar(select(Purchase).where(Purchase.token == purchase_token))
        if not purchase:
            abort(404)

        tickets = list(
            s.scalars(select(Ticket).where(Ticket.purchase_id == purchase.id))
        )

    ok_all = True
    for t in tickets:
        if not t.png_path:
            continue

        local_path = str(t.png_path)
        remote_name = local_path.split("/")[-1].split("\\")[-1]

        ok, info = upload_file(local_path, remote_filename=remote_name)
        if not ok:
            ok_all = False

    if ok_all:
        flash("Upload FTP concluído ✅", "success")
    else:
        flash("Upload FTP concluído com erros. Veja o log do Render.", "warning")

    return redirect(url_for("tickets.purchase_public", token=purchase.token))
