"""Baut ein einheitliches, auffaelliges Titelbild: Farbverlauf + Glow + Icon + Kernaussage."""
import math

from PIL import Image, ImageDraw, ImageFilter, ImageFont

import config
from video.icon_asset import get_or_build_icon

WIDTH, HEIGHT = 1280, 720
_FONT_PATH = config.DRAWTEXT_FONT_FILE.replace("\\:", ":")


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))


def _gradient_background(c0: str, c1: str, size=(WIDTH, HEIGHT)) -> Image.Image:
    w, h = size
    top, bottom = _hex_to_rgb(c0), _hex_to_rgb(c1)
    img = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(img)
    for y in range(h):
        t = y / h
        color = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        draw.line([(0, y), (w, y)], fill=color)
    return img.convert("RGBA")


def _add_radial_glow(img: Image.Image, center, accent, radius, strength=110) -> Image.Image:
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    cx, cy = center
    gdraw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=(*accent, strength))
    glow = glow.filter(ImageFilter.GaussianBlur(radius // 2))
    return Image.alpha_composite(img, glow)


def _diagonal_accent_stripes(img: Image.Image, accent) -> Image.Image:
    w, h = img.size
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for i, alpha in [(0, 45), (60, 30)]:
        draw.polygon(
            [(w - 40 + i, 0), (w + 300 + i, 0), (w - 260 + i, h), (w - 600 + i, h)],
            fill=(*accent, alpha),
        )
    return Image.alpha_composite(img, overlay)


def _wrap_text(draw, text: str, font, max_width: int) -> list[str]:
    words = text.split()
    lines, cur = [], ""
    for w in words:
        test = (cur + " " + w).strip()
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] > max_width and cur:
            lines.append(cur)
            cur = w
        else:
            cur = test
    if cur:
        lines.append(cur)
    return lines


def _draw_outlined_text(draw, xy, text, font, fill, outline_color, outline_width=6):
    x, y = xy
    for dx in range(-outline_width, outline_width + 1, 2):
        for dy in range(-outline_width, outline_width + 1, 2):
            if dx or dy:
                draw.text((x + dx, y + dy), text, font=font, fill=outline_color)
    draw.text((x, y), text, font=font, fill=fill)


def build_thumbnail(headline: str, out_path: str) -> str:
    c0, c1 = config.THUMBNAIL_COLORS
    accent = _hex_to_rgb(config.THUMBNAIL_ACCENT_COLOR)

    img = _gradient_background(c0, c1)
    img = _diagonal_accent_stripes(img, accent)
    img = _add_radial_glow(img, center=(WIDTH - 260, 200), accent=accent, radius=320, strength=100)

    # Icon oben rechts, leicht gedreht
    icon = Image.open(get_or_build_icon()).convert("RGBA")
    icon = icon.resize((360, 360))
    icon = icon.rotate(-8, expand=True, resample=Image.BICUBIC)
    img.alpha_composite(icon, (WIDTH - icon.width + 40, -40))

    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(_FONT_PATH, 100)
    max_text_width = WIDTH - 480
    lines = _wrap_text(draw, headline.upper(), font, max_text_width)
    line_height = 112
    total_h = len(lines) * line_height
    y = (HEIGHT - total_h) // 2 + 20

    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        x = 70
        _draw_outlined_text(draw, (x, y), line, font, fill=(255, 255, 255), outline_color=(*accent, 255)[:3])
        y += line_height

    # Akzent-Balken + Kanalname unten
    draw.rectangle([40, HEIGHT - 78, 46, HEIGHT - 34], fill=accent)
    small_font = ImageFont.truetype(_FONT_PATH, 34)
    draw.text((60, HEIGHT - 76), config.INTRO_CHANNEL_NAME.upper(), font=small_font, fill=accent)

    img.convert("RGB").save(out_path, quality=93)
    return out_path


def build_cover(headline: str, out_path: str, size=(1080, 1920)) -> str:
    """Hochformat-Cover (9:16) im Stil des Thumbnails - wird als erste Frames in
    Shorts eingeblendet, damit die Kachel im Shorts-Tab einheitlich aussieht."""
    w, h = size
    accent = _hex_to_rgb(config.THUMBNAIL_ACCENT_COLOR)

    img = _gradient_background(*config.THUMBNAIL_COLORS, size=size)
    img = _diagonal_accent_stripes(img, accent)
    img = _add_radial_glow(img, center=(w // 2, 520), accent=accent, radius=520, strength=100)

    icon = Image.open(get_or_build_icon()).convert("RGBA").resize((480, 480))
    icon = icon.rotate(-8, expand=True, resample=Image.BICUBIC)
    img.alpha_composite(icon, ((w - icon.width) // 2, 300))

    draw = ImageDraw.Draw(img)
    max_w = w - 180
    font_size = 140
    while True:
        font = ImageFont.truetype(_FONT_PATH, font_size)
        lines = _wrap_text(draw, headline.upper(), font, max_w)
        widest = max(draw.textbbox((0, 0), ln, font=font)[2] for ln in lines)
        if widest <= max_w or font_size <= 70:
            break
        font_size -= 6
    line_height = int(font_size * 1.13)
    y = 900 + max(0, (3 - len(lines))) * line_height // 2
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        x = (w - (bbox[2] - bbox[0])) // 2
        _draw_outlined_text(draw, (x, y), line, font, fill=(255, 255, 255), outline_color=accent, outline_width=8)
        y += line_height

    # Kanalname deutlich oberhalb der von der Shorts-Oberflaeche verdeckten Zone
    small_font = ImageFont.truetype(_FONT_PATH, 48)
    name = config.INTRO_CHANNEL_NAME.upper()
    bbox = draw.textbbox((0, 0), name, font=small_font)
    draw.rectangle([(w - 420) // 2, 1440, (w + 420) // 2, 1446], fill=accent)
    draw.text(((w - (bbox[2] - bbox[0])) // 2, 1470), name, font=small_font, fill=accent)

    img.convert("RGB").save(out_path, quality=93)
    return out_path
