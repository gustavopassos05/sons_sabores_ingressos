# app.py
import os
from pathlib import Path
from dotenv import load_dotenv
from flask import Flask

from models import Base
from db import engine

from routes.purchase import bp_purchase
from routes.tickets import bp_tickets
from routes.ftp import bp_ftp
from routes.webhooks import bp_webhooks
from routes.admin import bp_admin
from app_services.finalize_purchase import finalize_purchase_factory
from routes.admin_tickets import bp_admin_tickets
from routes.admin_pending import bp_admin_pending
from routes.admin_panel import bp_admin_panel
from routes.admin_auth import bp_admin_auth
from routes.admin_settings import bp_admin_settings
from routes.admin_purchases import bp_admin_purchases
from routes.admin_delete import bp_admin_delete
from routes.admin_shows import bp_admin_shows
from routes.home import bp_home
from routes.mercadopago import bp_mp


load_dotenv()

def create_app() -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "change-me")

    app.config["APP_NAME"] = os.getenv("APP_NAME", "Sons & Sabores · Ingressos")
    app.config["BASE_URL"] = (os.getenv("BASE_URL", "http://127.0.0.1:5005") or "").rstrip("/")

    # storage
    storage_default = "/tmp/sons_sabores_ingressos_storage"
    app.config["STORAGE_DIR"] = Path(os.getenv("STORAGE_DIR", storage_default)).resolve()

    app.config["TICKET_BASE_IMAGE_PATH"] = Path(
        os.getenv("TICKET_BASE_IMAGE_PATH", "static/ticket_base.png")
    ).resolve()

    # cria tabelas
    Base.metadata.create_all(engine)

    # ✅ BLUEPRINTS
    app.register_blueprint(bp_home)        # ← AGORA ESSA É A HOME REAL
    app.register_blueprint(bp_purchase)
    app.register_blueprint(bp_tickets)
    app.register_blueprint(bp_ftp)
    app.register_blueprint(bp_webhooks)
    app.register_blueprint(bp_admin)
    app.register_blueprint(bp_admin_tickets)
    app.register_blueprint(bp_admin_pending)
    app.register_blueprint(bp_admin_panel)
    app.register_blueprint(bp_admin_auth)
    app.register_blueprint(bp_admin_settings)
    app.register_blueprint(bp_admin_purchases)
    app.register_blueprint(bp_admin_delete)
    app.register_blueprint(bp_admin_shows)
    app.register_blueprint(bp_mp)

    # finalizador
    app.extensions["finalize_purchase"] = finalize_purchase_factory()

    return app

app = create_app()
