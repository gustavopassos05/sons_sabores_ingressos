# routes/admin_auth.py
import os
from functools import wraps
from flask import Blueprint, request, render_template, redirect, url_for, session, abort, flash

bp_admin_auth = Blueprint("admin_auth", __name__)

def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("admin_auth.admin_login"))
        return fn(*args, **kwargs)
    return wrapper

@bp_admin_auth.get("/admin/login")
def admin_login():
    return render_template("admin_login.html", app_name=os.getenv("APP_NAME", "Sons & Sabores"))

@bp_admin_auth.post("/admin/login")
def admin_login_post():
    pw = (request.form.get("password") or "").strip()
    correct = (os.getenv("ADMIN_PASSWORD") or "").strip()
    if not correct:
        abort(500, "ADMIN_PASSWORD não configurado.")

    if pw != correct:
        flash("Senha inválida.", "error")
        return redirect(url_for("admin_auth.admin_login"))

    session["is_admin"] = True
    return redirect(url_for("admin_panel.admin_home"))

@bp_admin_auth.post("/admin/logout")
def admin_logout():
    session.pop("is_admin", None)
    return redirect(url_for("admin_auth.admin_login"))
