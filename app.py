# app.py
import os
from pathlib import Path
from dotenv import load_dotenv
from flask import Flask, redirect, url_for

from models import Base  # se você usa Base.metadata.create_all
from db import engine    # vamos usar o engine do db.py

from routes.purchase import bp_purchase
from routes.tickets import bp_tickets
from routes.ftp import bp_ftp
from routes.webhooks import bp_webhooks
from app_services.finalize_purchase import finalize_purchase_factory
from app_services.payments.pagseguro_notify import bp_pagseguro_notify


load_dotenv()  # local ok; no Render as env vars vêm do painel

def create_app() -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "change-me")

    app.config["APP_NAME"] = os.getenv("APP_NAME", "Sons & Sabores · Ingressos")
    app.config["BASE_URL"] = (os.getenv("BASE_URL", "http://127.0.0.1:5005") or "").rstrip("/")

    # ✅ NO RENDER, use /tmp (plano free não tem disk)
    storage_default = "/tmp/sons_sabores_ingressos_storage"
    app.config["STORAGE_DIR"] = Path(os.getenv("STORAGE_DIR", storage_default)).resolve()

    app.config["TICKET_BASE_IMAGE_PATH"] = Path(
        os.getenv("TICKET_BASE_IMAGE_PATH", "static/ticket_base.png")
    ).resolve()

    app.config["SHOWS"] = [
        "Jimmy Duchowny e Mark Lambert",
        "Rodrigo Quintela",
        "Alexandre Araújo",
        "Thiago Delegado",
        "Hudson de Souza",
        "Alexandre Rezende",
        "Marilton",
    ]

    # ✅ cria tabelas (ok pro começo; depois você pode migrar para Alembic)
    Base.metadata.create_all(engine)

    # home
    @app.get("/")
    def home():
        default_slug = os.getenv("DEFAULT_EVENT_SLUG", "sons-e-sabores")
        return redirect(url_for("purchase.buy", event_slug=default_slug))

    # registra blueprints
    app.register_blueprint(bp_purchase)
    app.register_blueprint(bp_tickets)
    app.register_blueprint(bp_ftp)
    app.register_blueprint(bp_webhooks)
    app.extensions["finalize_purchase"] = finalize_purchase_factory(app)
    app.register_blueprint(bp_pagseguro_notify)


    return app


app = create_app()
