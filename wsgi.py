# wsgi.py
import os

# se você tem create_app em app.py
from app import create_app

app = create_app()

# opcional: healthcheck
@app.get("/health")
def health():
    return {"ok": True}
