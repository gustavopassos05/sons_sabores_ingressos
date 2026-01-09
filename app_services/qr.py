# payments/qr.py
import qrcode
from PIL import Image


def make_qr_image(url: str, size_px: int = 360) -> Image.Image:
    img = qrcode.make(url).convert("RGBA")
    return img.resize((size_px, size_px))
