# db.py
import os
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL não configurado nas env vars do Render.")

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=int(os.getenv("DB_POOL_RECYCLE_SECONDS", "280")),
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,  # ✅ importante
)



@contextmanager
def db():
    """Uso: with db() as s: ..."""
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
