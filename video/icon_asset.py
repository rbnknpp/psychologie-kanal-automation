"""Baut (einmalig, gecacht) ein Icon-PNG: Emoji auf weichem Glow-Kreis in Akzentfarbe."""
from PIL import Image, ImageDraw, ImageFilter, ImageFont

import config

_EMOJI_FONT = "C:/Windows/Fonts/seguiemj.ttf"


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))


def build_icon(out_path: str, size: int = 420) -> str:
    accent = _hex_to_rgb(config.THUMBNAIL_ACCENT_COLOR)

    # weicher Glow-Kreis im Hintergrund
    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    margin = size // 6
    gdraw.ellipse([margin, margin, size - margin, size - margin], fill=(*accent, 140))
    glow = glow.filter(ImageFilter.GaussianBlur(size // 12))

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    img.alpha_composite(glow)

    font = ImageFont.truetype(_EMOJI_FONT, int(size * 0.62))
    draw = ImageDraw.Draw(img)
    bbox = draw.textbbox((0, 0), config.THUMBNAIL_EMOJI, font=font, embedded_color=True)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (size - w) // 2 - bbox[0]
    y = (size - h) // 2 - bbox[1]
    draw.text((x, y), config.THUMBNAIL_EMOJI, font=font, embedded_color=True)

    img.save(out_path)
    return out_path


def get_or_build_icon() -> str:
    icon_path = config.ASSETS_DIR / "icon.png"
    if not icon_path.exists():
        build_icon(str(icon_path))
    return str(icon_path)
