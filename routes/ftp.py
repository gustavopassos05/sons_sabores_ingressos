# routes/ftp.py
from flask import Blueprint, redirect, url_for, flash, abort
from sqlalchemy import select

from models import Purchase, Ticket
from app_services.ftp_uploader import upload_file
from app import db

bp = Blueprint("ftp", __name__)


@bp.post("/admin/ftp/push/<purchase_token>")
def ftp_push_purchase(purchase_token: str):
    # aqui você pode plugar sua checagem de admin (is_logged_in)
    with db() as s:
        purchase = s.scalar(select(Purchase).where(Purchase.token == purchase_token))
        if not purchase:
            abort(404)

        tickets = list(s.scalars(select(Ticket).where(Ticket.purchase_id == purchase.id)))

    ok_all = True
    for t in tickets:
        if t.png_path:
            ok, info = upload_file(t.png_path, remote_filename=t.png_path.split("/")[-1].split("\\")[-1])
            if not ok:
                ok_all = False

    if ok_all:
        flash("Upload FTP concluído ✅", "success")
    else:
        flash("Upload FTP concluído com erros. Veja o log do Render.", "warning")

    return redirect(url_for("tickets.purchase_public", token=purchase.token))
