# config_ticket.py
from pathlib import Path

# ---------- ARQUIVOS ----------
TICKET_BASE_IMAGE = Path("static/ticket_base.png")
BASE_DIR = Path(__file__).resolve().parent

# =========================
# FONTES
# =========================
FONT_SHOW = BASE_DIR / "static" / "fonts" / "Kalam-Regular.ttf"
FONT_NAMES = BASE_DIR / "static" / "fonts" / "Poppins-Regular.ttf"

# Se quiser variar depois:
# FONT_NAMES_BOLD = BASE_DIR / "static" / "fonts" / "Poppins-SemiBold.ttf"

# ---------- FONTES ----------
FONT_SIZE_SHOW = 65
FONT_SIZE_NAMES = 60

LINE_SPACING_NAMES = 12

# ---------- POSIÇÕES (em px) ----------
SHOW_Y = 380        # nome do show
NAMES_Y = 500         # comprador + acompanhantes

# ---------- QR CODE ----------
QR_SIZE_PX = 360        # já está ótimo para 1080x1920
QR_Y_FACTOR = 0.74      # centro inferior
QR_Y_OFFSET = -200        # ajuste fino (use depois se quiser)

# ---------- CORES ----------
TEXT_COLOR = (0, 0, 0, 255)
