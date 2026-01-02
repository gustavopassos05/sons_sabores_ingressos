import os
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, redirect, url_for, session
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from werkzeug.security import generate_password_hash, check_password_hash

from models import Base
from routes.purchase import bp_purchase

from routes.tickets import bp_tickets
from routes.ftp import bp_ftp

from finalize_purchase import finalize_purchase_factory  # o arquivo com o factory acima

load_dotenv()


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "change-me")

    app.config["APP_NAME"] = os.getenv("APP_NAME", "Sons & Sabores · Ingressos")
    app.config["BASE_URL"] = os.getenv("BASE_URL", "http://127.0.0.1:5005").rstrip("/")
    app.config["STORAGE_DIR"] = Path(os.getenv("STORAGE_DIR", "/var/data/sons_sabores_ingressos")).resolve()
    app.config["TICKET_BASE_IMAGE_PATH"] = Path(os.getenv("TICKET_BASE_IMAGE_PATH", "static/ticket_base.png")).resolve()

    app.config["SHOWS"] = [
        "Jimmy Duchowny e Mark Lambert",
        "Rodrigo Quintela",
        "Alexandre Araújo",
        "Thiago Delegado",
        "Hudson de Souza",
        "Alexandre Rezende",
        "Marilton",
    ]

    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL não configurado.")

    engine = create_engine(db_url, pool_pre_ping=True, pool_recycle=280)
    SessionLocal = sessionmaker(bind=engine)
    Base.metadata.create_all(engine)

    def db():
        return SessionLocal()

    app.extensions["db"] = db

    # ----- admin simples -----
    admin_user = os.getenv("ADMIN_USERNAME", "admin")
    admin_pass = os.getenv("ADMIN_PASSWORD", "change-me")
    admin_hash = generate_password_hash(admin_pass)

    def is_logged_in() -> bool:
        return session.get("admin_logged_in") is True

    def require_login():
        if not is_logged_in():
            return redirect(url_for("admin_login"))
        return None

    app.extensions["require_login"] = require_login

    # finalize_purchase (gera tickets + arquivos local)
    app.extensions["finalize_purchase"] = finalize_purchase_factory(app)

    # home
    @app.get("/")
    def home():
        default_slug = os.getenv("DEFAULT_EVENT_SLUG", "sons-e-sabores")
        return redirect(url_for("purchase.buy", event_slug=default_slug))

    # login admin (exemplo mínimo)
    @app.get("/admin/login")
    def admin_login():
        return "TODO: template login"

    @app.post("/admin/login")
    def admin_login_post():
        return "TODO"

    # registra blueprints
    app.register_blueprint(bp_purchase)
    app.register_blueprint(bp_tickets)
    app.register_blueprint(bp_ftp)

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5005, debug=True)


