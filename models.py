from datetime import datetime
from sqlalchemy import (
    String, Integer, DateTime, Text, ForeignKey, UniqueConstraint
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, declarative_base

Base = declarative_base()

class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    date_text: Mapped[str] = mapped_column(String(120), default="", nullable=True)

    tickets: Mapped[list["Ticket"]] = relationship(back_populates="event")

class Purchase(Base):
    __tablename__ = "purchases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), nullable=False)

    # ✅ token público para URL /purchase/<token>
    token: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)

    show_name: Mapped[str] = mapped_column(String(180), nullable=False)

    buyer_name: Mapped[str] = mapped_column(String(160), nullable=False)
    buyer_email: Mapped[str] = mapped_column(String(200), nullable=True)
    buyer_phone: Mapped[str] = mapped_column(String(40), nullable=True)
    buyer_cpf: Mapped[str] = mapped_column(String(30), nullable=True)
    buyer_cpf_digits: Mapped[str] = mapped_column(String(14), nullable=True)  # ✅ ADD AQUI


    guests_text: Mapped[str] = mapped_column(Text, nullable=True)

    qty_adult: Mapped[int] = mapped_column(Integer, default=1)
    qty_child: Mapped[int] = mapped_column(Integer, default=0)

    status: Mapped[str] = mapped_column(String(30), default="pending_payment")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    tickets: Mapped[list["Ticket"]] = relationship(back_populates="purchase")
    payments: Mapped[list["Payment"]] = relationship(back_populates="purchase")


class Ticket(Base):
    __tablename__ = "tickets"
    __table_args__ = (UniqueConstraint("token", name="uq_ticket_token"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), nullable=False)
    purchase_id: Mapped[int] = mapped_column(ForeignKey("purchases.id"), nullable=True)

    show_name: Mapped[str] = mapped_column(String(180), nullable=False)

    buyer_name: Mapped[str] = mapped_column(String(160), nullable=False)
    buyer_email: Mapped[str] = mapped_column(String(200), nullable=True)
    buyer_phone: Mapped[str] = mapped_column(String(40), nullable=True)

    # ✅ nome do ingresso (um por pessoa)
    person_name: Mapped[str] = mapped_column(String(160), nullable=False)
    person_type: Mapped[str] = mapped_column(String(20), default="guest")  # buyer/guest

    token: Mapped[str] = mapped_column(String(80), nullable=False)

    png_path: Mapped[str] = mapped_column(String(400), nullable=True)
    pdf_path: Mapped[str] = mapped_column(String(400), nullable=True)

    status: Mapped[str] = mapped_column(String(20), default="issued")  # issued/checked_in/cancelled
    issued_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    checked_in_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    checked_in_by: Mapped[str] = mapped_column(String(80), nullable=True)

    event: Mapped["Event"] = relationship(back_populates="tickets")
    purchase: Mapped["Purchase"] = relationship(back_populates="tickets")


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    purchase_id: Mapped[int] = mapped_column(ForeignKey("purchases.id"), nullable=True)

    provider: Mapped[str] = mapped_column(String(40), nullable=False)  # pagbank
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(10), default="BRL")
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/paid/failed

    external_id: Mapped[str] = mapped_column(String(120), nullable=True)  # order id PagBank
    checkout_url: Mapped[str] = mapped_column(String(600), nullable=True)

    qr_text: Mapped[str] = mapped_column(Text, nullable=True)
    qr_image_base64: Mapped[str] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    paid_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    purchase: Mapped["Purchase"] = relationship(back_populates="payments")

    tickets_pdf_url: Mapped[str] = mapped_column(String(500), nullable=True)
    tickets_zip_url: Mapped[str] = mapped_column(String(500), nullable=True)
    tickets_generated_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

class AdminSetting(Base):
    __tablename__ = "admin_settings"
    __table_args__ = (UniqueConstraint("key", name="uq_admin_settings_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(80), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=True)
