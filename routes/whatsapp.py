from flask import Blueprint, redirect, current_app, render_template_string, url_for
from flask_login import login_required

from models import db, Reserva
from app_services.utils_whatsapp import whatsapp_link, msg_para_borogodo, msg_para_cliente

bp_whats = Blueprint("whats", __name__)

BOROGODO_WHATS = "5531999613652"  # seu número (E.164)


@bp_whats.route("/reserva/<int:reserva_id>/whats/borogodo")
@login_required
def whats_borogodo(reserva_id):
    r = db.session.get(Reserva, reserva_id)
    if not r:
        return redirect(url_for("index"))
    return redirect(whatsapp_link(BOROGODO_WHATS, msg_para_borogodo(r)))


@bp_whats.route("/reserva/<int:reserva_id>/whats/cliente")
@login_required
def whats_cliente(reserva_id):
    r = db.session.get(Reserva, reserva_id)
    if not r:
        return redirect(url_for("index"))
    return redirect(whatsapp_link(r.telefone, msg_para_cliente(r)))


@bp_whats.route("/reserva/<int:reserva_id>/whats/disparar")
@login_required
def whats_disparar(reserva_id):
    """
    "Disparo automático" SEM API: abre 2 links wa.me (Borogodó + Cliente)
    e depois volta para /admin (ou onde você quiser).
    """
    r = db.session.get(Reserva, reserva_id)
    if not r:
        return redirect(url_for("index"))

    link_boro = whatsapp_link(BOROGODO_WHATS, msg_para_borogodo(r))
    link_cli = whatsapp_link(r.telefone, msg_para_cliente(r))

    back_url = url_for("admin.admin_home") if "admin.admin_home" in current_app.view_functions else url_for("index")

    html = f"""
    <!doctype html>
    <html lang="pt-br">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>Disparando WhatsApp…</title>
      <style>
        body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;margin:20px;max-width:720px}}
        .card{{border:1px solid #eee;border-radius:12px;padding:14px}}
        a{{word-break:break-all}}
        .btn{{display:inline-block;padding:10px 12px;border:1px solid #ccc;border-radius:10px;text-decoration:none}}
      </style>
    </head>
    <body>
      <h2>Disparando WhatsApp…</h2>
      <div class="card">
        <p>Se o navegador bloquear pop-up, clique manualmente:</p>
        <p><a class="btn" href="{link_boro}" target="_blank" rel="noopener">Whats Borogodó</a></p>
        <p><a class="btn" href="{link_cli}" target="_blank" rel="noopener">Whats Cliente</a></p>
        <p><a href="{back_url}">Voltar</a></p>
      </div>

      <script>
        // tenta abrir as duas abas/janelas
        window.open("{link_boro}", "_blank");
        setTimeout(() => window.open("{link_cli}", "_blank"), 400);
        // volta sozinho depois de um tempinho
        setTimeout(() => window.location.href = "{back_url}", 1200);
      </script>
    </body>
    </html>
    """
    return render_template_string(html)
