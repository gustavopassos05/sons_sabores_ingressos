# routes/ftp.py
from flask import Blueprint, redirect, url_for, flash, abort
from sqlalchemy import select

from db import db
from models import Purchase, Ticket
from app_services.ftp_uploader import upload_file

bp_ftp = Blueprint("ftp", __name__)

@bp_ftp.post("/admin/ftp/push/<purchase_token>")
def ftp_push_purchase(purchase_token: str):
    with db() as s:
        purchase = s.scalar(select(Purchase).where(Purchase.token == purchase_token))
        if not purchase:
            abort(404)
        tickets = list(s.scalars(select(Ticket).where(Ticket.purchase_id == purchase.id)))

    ok_all = True
    for t in tickets:
        if t.png_path:
            ok, _info = upload_file(t.png_path, remote_filename=str(t.png_path).split("/")[-1].split("\\")[-1])
            if not ok:
                ok_all = False

    flash("Upload FTP concluído ✅" if ok_all else "Upload FTP com erros. Veja o log do Render.", "success" if ok_all else "warning")
    return redirect(url_for("tickets.purchase_public", token=purchase.token))
