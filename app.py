# app.py
import os
from pathlib import Path
from dotenv import load_dotenv
from flask import Flask

from sqlalchemy import select, func

from models import Base, Purchase
from db import engine, db

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
from routes.admin_reservations import bp_admin_reservations
from routes.whatsapp import bp_whats


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
    app.register_blueprint(bp_home)
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
    app.register_blueprint(bp_admin_reservations)
    app.register_blueprint(bp_whats)


    # ✅ pluga o finalizador
    app.extensions["finalize_purchase"] = finalize_purchase_factory()

    # ✅ badges globais pro admin
    @app.context_processor
    def inject_admin_badges():
        data = {}

        try:
            with db() as s:
                total_pessoas = s.scalar(
                    select(func.coalesce(func.sum(Purchase.ticket_qty), 0))
                    .where(Purchase.status.in_(["reserved", "paid"]))
                ) or 0
            data["admin_reservas_pessoas_total"] = int(total_pessoas)
        except Exception:
            data["admin_reservas_pessoas_total"] = 0

        return data

    return app  # ✅ AGORA ESTÁ NO LUGAR CERTO


app = create_app()
