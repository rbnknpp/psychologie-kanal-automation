"""Pflege-TikToks im News-Stil (seit 17.11.2026, Vorlage von Robin aus Dropbox).

Look: dunkler Hintergrund, isometrische 3D-Illustrationen (Flux über Edge-Funktion site-bilder),
Infokarten (Uhrzeit, Tabelle, Liste, große Zahl), Untertitel-Pille unten, ruhige Männerstimme (edge-tts),
Schnitt etwa alle 2-3 s. Inhalt: echte Pflege-News mit Quelle (Claude + Websuche).

Aufruf:  python -m pflege.tiktok_news --count 3        (bauen + als TikTok-Entwurf einreihen)
         python -m pflege.tiktok_news --test            (1 Video, nur rendern)
         python -m pflege.tiktok_news --script x.json   (vorhandenes Skript rendern)
"""
import argparse
import asyncio
import base64
import datetime as dt
import hashlib
import io
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import config  # noqa: E402

HERE = Path(__file__).resolve().parent
STATE = HERE / "tiktok_state.json"
OUT = ROOT / "output" / "pflege_tiktok"
TZ = ZoneInfo("Europe/Berlin")
W, H, FPS = 1080, 1920, 30
VOICE = os.getenv("PFLEGE_TT_VOICE", "de-DE-ConradNeural")
# Seit 23.11.2026: günstiges Modell für Recherche + Skript (Kosten), höchstens 3 Websuchen
NEWS_MODEL = os.getenv("PFLEGE_NEWS_MODEL", "claude-haiku-5-5")
RATE = os.getenv("PFLEGE_TT_RATE", "+6%")
BILDER = "https://kfiiaqjzzwwacthzrcut.supabase.co/functions/v1/site-bilder"
GAMING_UPLOAD = "https://kfiiaqjzzwwacthzrcut.supabase.co/functions/v1/gaming-upload"
STYLE = ("realistic 3D render, detailed miniature diorama with lifelike materials and textures, "
         "cinematic soft lighting, gentle depth of field, natural colors, high detail, "
         "isolated on plain flat dark charcoal background, no text, no letters, no logos, centered composition")

BG_TOP, BG_BOT = (36, 39, 42), (22, 24, 26)
WHITE, DARK, MUTED = (245, 245, 243), (30, 33, 36), (150, 156, 160)
GREEN, GREEN_T = (46, 107, 85), (215, 240, 228)
ORANGE, RED, TEAL = (232, 128, 74), (214, 64, 64), (92, 214, 214)
CARD = (238, 239, 236)

IMG_W, IMG_H = 860, 680          # Fläche für die Illustration (unter der Textzone)
PILL_Y = 1330                    # Untertitel-Pille (oberhalb der TikTok-Bedienelemente)
FONT = HERE / "fonts" / "Inter.ttf"
EMOJI_FONTS = ["/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf", r"C:\Windows\Fonts\seguiemj.ttf"]
_font_cache, _emoji_cache = {}, {}


def font(size, weight="Bold"):
    k = (size, weight)
    if k not in _font_cache:
        f = ImageFont.truetype(str(FONT), size)
        try:
            f.set_variation_by_name(weight)
        except Exception:  # noqa: BLE001
            pass
        _font_cache[k] = f
    return _font_cache[k]


def emoji(ch, size):
    """Farbiges Emoji als RGBA-Bild (Noto nur in 109 px, daher skalieren)."""
    k = (ch, size)
    if k in _emoji_cache:
        return _emoji_cache[k]
    im = None
    for p in EMOJI_FONTS:
        if Path(p).exists():
            try:
                f = ImageFont.truetype(p, 109)
                tmp = Image.new("RGBA", (160, 160), (0, 0, 0, 0))
                ImageDraw.Draw(tmp).text((80, 80), ch, font=f, embedded_color=True, anchor="mm")
                bb = tmp.getbbox()
                if bb:
                    im = tmp.crop(bb)
                    im = im.resize((max(1, int(im.width * size / max(im.size))), max(1, int(im.height * size / max(im.size)))), Image.LANCZOS)
                break
            except Exception:  # noqa: BLE001
                continue
    _emoji_cache[k] = im
    return im


def tw(d, text, f):
    return d.textlength(text, font=f)


EMOJI_RE = re.compile(r"[\U0001F000-\U0001FAFF☀-➿⬀-⯿️]+")


def etext(lay, xy, text, f, fill, size):
    """Text mit führendem Emoji (Emoji als Bild, Rest als Schrift)."""
    d = ImageDraw.Draw(lay)
    x, y = xy
    m = EMOJI_RE.match(text.strip())
    if m:
        e = emoji(m.group(0).replace("️", ""), size)
        if e:
            lay.alpha_composite(e, (int(x), int(y + (f.size - size) * 0.6)))
            x += size + 14
        text = text.strip()[m.end():].strip()
    d.text((x, y), EMOJI_RE.sub("", text), font=f, fill=fill)


def wrap(d, text, f, maxw):
    lines, cur = [], ""
    for w in text.split():
        t = (cur + " " + w).strip()
        if tw(d, t, f) <= maxw or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def rr(d, box, r, fill, outline=None, width=0):
    d.rounded_rectangle(box, radius=r, fill=fill, outline=outline, width=width)


def shadowed(layer, radius=18, offset=(0, 10), alpha=110):
    """Weicher Schatten unter einer RGBA-Ebene."""
    a = layer.split()[3].point(lambda v: min(alpha, v))
    sh = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    sh.putalpha(a)
    sh = sh.filter(ImageFilter.GaussianBlur(radius))
    out = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    out.alpha_composite(sh, offset)
    out.alpha_composite(layer)
    return out


# ---------------------------------------------------------------- Hintergrund und Bilder
def background():
    y = np.linspace(0, 1, H)[:, None]
    top, bot = np.array(BG_TOP, float), np.array(BG_BOT, float)
    g = top + (bot - top) * y
    img = np.repeat(g[:, None, :], W, axis=1)
    # zarte Lichtkegel von oben (wie im Vorbild)
    xx = np.arange(W)[None, :]
    yy = np.arange(H)[:, None]
    beam = np.zeros((H, W))
    for cx in (300, 560, 820):
        beam += np.clip(1 - np.abs(xx - cx - (yy - 300) * 0.25) / 90, 0, 1) * np.clip(1 - yy / 1500, 0, 1) * 6
    img = np.clip(img + beam[:, :, None], 0, 255).astype(np.uint8)
    return Image.fromarray(img, "RGB").convert("RGBA")


def isolate(img):
    """Flux-Bild weich in den dunklen Hintergrund einblenden (runde, weiche Maske)."""
    img = img.convert("RGB").resize((900, 900), Image.LANCZOS)
    arr = np.asarray(img).astype(float)
    corner = np.median(np.concatenate([arr[:40, :40].reshape(-1, 3), arr[:40, -40:].reshape(-1, 3)]), axis=0)
    # Hintergrund des Bildes Richtung Szenenfarbe ziehen
    dist = np.linalg.norm(arr - corner, axis=2)
    fg = np.clip((dist - 10) / 35, 0, 1)
    yy, xx = np.mgrid[0:900, 0:900]
    r = np.sqrt((xx - 450) ** 2 + (yy - 450) ** 2) / 450
    vign = np.clip((1.02 - r) / 0.25, 0, 1)
    alpha = np.clip(np.maximum(fg, 0.0) * vign * 255, 0, 255).astype(np.uint8)
    alpha = Image.fromarray(alpha).filter(ImageFilter.GaussianBlur(3))
    out = img.convert("RGBA")
    out.putalpha(alpha)
    # auf den Inhalt zuschneiden und in die Bildfläche (IMG_W x IMG_H) einpassen
    bb = out.split()[3].point(lambda v: 255 if v > 40 else 0).getbbox()
    if bb:
        out = out.crop(bb)
    s = min(IMG_W / out.width, IMG_H / out.height)
    out = out.resize((max(1, int(out.width * s)), max(1, int(out.height * s))), Image.LANCZOS)
    canvas = Image.new("RGBA", (IMG_W, IMG_H), (0, 0, 0, 0))
    canvas.alpha_composite(out, ((IMG_W - out.width) // 2, (IMG_H - out.height) // 2))
    return canvas


def fetch_image(prompt, cache_dir):
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(prompt.encode()).hexdigest()[:16]
    p = cache_dir / f"{key}.png"
    if p.exists():
        return Image.open(p).convert("RGBA")
    tok = os.environ.get("PFLEGE_UPLOAD_TOKEN", "").strip()
    for attempt in range(3):
        try:
            r = requests.post(BILDER, json={"prompt": f"{prompt}, {STYLE}"}, headers={"x-pflege-token": tok}, timeout=120).json()
            if "b64" in r:
                im = isolate(Image.open(io.BytesIO(base64.b64decode(r["b64"]))))
                im.save(p)
                return im
            print("  Bild-Fehler:", str(r)[:200])
        except Exception as e:  # noqa: BLE001
            print("  Bild-Fehler:", e)
    return None


# ---------------------------------------------------------------- Elemente (animierte Bausteine)
# Seit 20.11.2026 (Robin: „Schrift über der Grafik, zu wenig Motion Graphics, 0815“):
# feste Zonen (Text/Karten oben y 140–620, Illustration darunter), jedes Element besteht aus Teilen
# mit eigener Einflug-Animation (gestaffelt), Zahlen zählen hoch, Balken füllen sich, Listen/Tabellen bauen sich auf.
TOP_Y = 150                      # obere Kante der Text-/Kartenzone
IMG_CY = 975                     # Mitte der Illustration (unter der Textzone, über der Pille)


class Part:
    """Ausgeschnittene RGBA-Ebene mit Position, Startverzögerung und Animationsart."""
    __slots__ = ("img", "x", "y", "delay", "anim")

    def __init__(self, img, x, y, delay=0.0, anim="pop"):
        self.img, self.x, self.y, self.delay, self.anim = img, int(x), int(y), delay, anim


def part(layer, delay=0.0, anim="pop", shadow=False):
    if shadow:
        layer = shadowed(layer)
    bb = layer.getbbox()
    if not bb:
        return None
    return Part(layer.crop(bb), bb[0], bb[1], delay, anim)


def blank():
    return Image.new("RGBA", (W, H), (0, 0, 0, 0))


def num_parse(s):
    """'0,9 %' -> (0.9, 1, '', ' %'); Jahreszahlen/Uhrzeiten zählen nicht hoch."""
    m = re.search(r"(\d+(?:[.,]\d+)?)", s or "")
    if not m or re.search(r"\d:\d", s) or re.fullmatch(r"\s*(19|20)\d\d\s*", s):
        return None
    raw = m.group(1)
    dec = len(raw.split(",")[1]) if "," in raw else 0
    try:
        val = float(raw.replace(".", "").replace(",", ".")) if dec or "." in raw else float(raw)
    except ValueError:
        return None
    return val, dec, s[:m.start()], s[m.end():]


def num_fmt(v, dec):
    t = f"{v:,.{dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return t


def el_hook(text):
    parts = []
    f = font(80, "ExtraBold")
    d = ImageDraw.Draw(blank())
    lines = wrap(d, text, f, 880)[:4]
    lh = 100
    for i, ln in enumerate(lines):
        lay = blank()
        d = ImageDraw.Draw(lay)
        w = tw(d, ln, f)
        x, y = (W - w) / 2, TOP_Y + 110 + i * lh
        rr(d, (x - 24, y - 8, x + w + 24, y + lh - 6), 12, WHITE)
        d.text((x, y), ln, font=f, fill=DARK)
        parts.append(part(lay, 0.08 + i * 0.12, "left"))
    # kleiner grüner Akzentbalken darüber
    lay = blank()
    ImageDraw.Draw(lay).rounded_rectangle((W / 2 - 70, TOP_Y + 60, W / 2 + 70, TOP_Y + 74), radius=7, fill=GREEN_T)
    parts.insert(0, part(lay, 0.0, "wipe"))
    return {"parts": parts}


def el_chip(icon, label, wert):
    lay = blank()
    d = ImageDraw.Draw(lay)
    fl, fv = font(34, "SemiBold"), font(86)
    w = max(tw(d, wert, fv), tw(d, label, fl) + 60) + 50
    x0, y0 = (W - w) / 2, TOP_Y
    rr(d, (x0, y0, x0 + w, y0 + 200), 22, CARD)
    d.rounded_rectangle((x0, y0, x0 + w, y0 + 64), radius=22, fill=GREEN, corners=(True, True, False, False))
    e = emoji(icon, 34) if icon else None
    lx = x0 + 22
    if e:
        lay.alpha_composite(e, (int(lx), int(y0 + 15)))
        lx += 46
    d.text((lx, y0 + 12), label, font=fl, fill=WHITE)
    n = num_parse(wert)
    if not n:
        d.text((x0 + 25, y0 + 78), wert, font=fv, fill=DARK)
    p = part(lay, 0.0, "pop", shadow=True)

    def dyn(base, ts):
        if not n:
            return
        k = min(1.0, max(0.0, (ts - 0.15) / 0.7))
        v = n[0] * (1 - (1 - k) ** 3)
        ImageDraw.Draw(base).text((x0 + 25, y0 + 78), f"{n[2]}{num_fmt(v, n[1])}{n[3]}", font=fv, fill=DARK)
    return {"parts": [p], "dyn": dyn if n else None}


def el_label(text):
    parts = []
    f = font(66, "ExtraBold")
    d = ImageDraw.Draw(blank())
    for i, ln in enumerate(wrap(d, text, f, 900)[:3]):
        lay = blank()
        ImageDraw.Draw(lay).text((W / 2, TOP_Y + 250 + i * 80), ln, font=f, fill=WHITE, anchor="mt")
        parts.append(part(lay, i * 0.1, "up"))
    return {"parts": parts}


def el_glow(icon, titel, unter):
    f = font(110, "ExtraBold")
    d = ImageDraw.Draw(blank())
    e = emoji(icon, 96) if icon else None
    tw_ = tw(d, titel, f) + (116 if e else 0)
    x = (W - tw_) / 2
    y = TOP_Y + 160
    glow = blank()
    ImageDraw.Draw(glow).text((x + (116 if e else 0), y), titel, font=f, fill=TEAL + (255,))
    glow = glow.filter(ImageFilter.GaussianBlur(20))
    gp = part(glow, 0.0, "fade")
    lay = blank()
    if e:
        lay.alpha_composite(e, (int(x), y + 10))
    ImageDraw.Draw(lay).text((x + (116 if e else 0), y), titel, font=f, fill=(170, 240, 244))
    parts = [gp, part(lay, 0.0, "pop")]
    if unter:
        lu = blank()
        ImageDraw.Draw(lu).text((W / 2, y + 150), unter, font=font(40, "SemiBold"), fill=WHITE, anchor="mt")
        parts.append(part(lu, 0.25, "up"))

    def dyn(base, ts):  # pulsierendes Leuchten
        if ts > 0.3 and gp:
            a = 0.35 + 0.35 * (0.5 + 0.5 * math.sin(ts * 4.2))
            g = gp.img.copy()
            g.putalpha(g.split()[3].point(lambda v: int(v * a)))
            base.alpha_composite(g, (gp.x, gp.y))
    return {"parts": parts, "dyn": dyn}


def el_tabelle(icon, titel, zeilen):
    fh, fr = font(40), font(40, "SemiBold")
    x0, y0, w = 90, TOP_Y, 900
    h = 90 + 76 * len(zeilen) + 20
    lay = blank()
    d = ImageDraw.Draw(lay)
    rr(d, (x0, y0, x0 + w, y0 + h), 22, CARD)
    e = emoji(icon, 40) if icon else None
    tx = x0 + 30
    if e:
        lay.alpha_composite(e, (tx, y0 + 26))
        tx += 54
    d.text((tx, y0 + 24), titel, font=fh, fill=DARK)
    parts = [part(lay, 0.0, "pop", shadow=True)]
    for i, z in enumerate(zeilen):
        rl = blank()
        rd = ImageDraw.Draw(rl)
        y = y0 + 96 + i * 76
        rd.line((x0 + 24, y - 8, x0 + w - 24, y - 8), fill=(214, 216, 212), width=2)
        etext(rl, (x0 + 40, y + 6), str(z[0]) if len(z) > 0 else "", fr, RED, 38)
        etext(rl, (x0 + 330, y + 6), str(z[1]) if len(z) > 1 else "", fr, DARK, 38)
        rd.text((x0 + w - 70, y + 4), "✓", font=font(44), fill=GREEN)
        parts.append(part(rl, 0.25 + i * 0.16, "left"))
    return {"parts": parts}


def el_liste(tag, titel, punkte, alt=0):
    """alt = Anzahl Punkte, die schon in der vorigen Szene standen (erscheinen ohne Animation)."""
    parts = []
    y = TOP_Y
    st = -1.0 if alt else 0.0
    if tag:
        lay = blank()
        d = ImageDraw.Draw(lay)
        ft = font(32, "SemiBold")
        w = tw(d, tag, ft) + 90
        rr(d, ((W - w) / 2, y, (W + w) / 2, y + 58), 29, GREEN)
        e = emoji("📰", 30)
        if e:
            lay.alpha_composite(e, (int((W - w) / 2 + 24), y + 14))
        d.text(((W - w) / 2 + 66, y + 10), tag, font=ft, fill=GREEN_T)
        parts.append(part(lay, st, "pop"))
        y += 100
    if titel:
        lay = blank()
        ImageDraw.Draw(lay).text((W / 2, y), titel, font=font(54), fill=WHITE, anchor="mt")
        parts.append(part(lay, st if alt else 0.1, "up"))
        y += 100
    for i, p in enumerate(punkte):
        lay = blank()
        d = ImageDraw.Draw(lay)
        rr(d, (70, y, W - 70, y + 104), 18, CARD)
        e = emoji(p.get("icon") or "•", 52) if p.get("icon") else None
        if e:
            lay.alpha_composite(e, (100, int(y + 26)))
        d.text((180, y + 26), EMOJI_RE.sub("", str(p.get("text", "")))[:38], font=font(44, "SemiBold"), fill=DARK)
        new = i >= alt
        parts.append(part(lay, (0.2 + (i - alt) * 0.14) if new else -1.0, "left", shadow=True))
        y += 128
    return {"parts": parts}


def el_zahl(oben, wert, balken):
    parts = []
    if oben:
        lay = blank()
        ImageDraw.Draw(lay).text((W / 2, TOP_Y + 330), oben, font=font(46), fill=WHITE, anchor="mt")
        parts.append(part(lay, 0.0, "up"))
    fv = font(160, "ExtraBold")
    n = num_parse(wert)
    yv = TOP_Y + 440
    if not n:
        lay = blank()
        ImageDraw.Draw(lay).text((W / 2, yv), wert, font=fv, fill=ORANGE, anchor="mt")
        parts.append(part(lay, 0.12, "pop"))
    b = None if balken is None else max(0.03, min(1.0, float(balken)))
    by = TOP_Y + 690

    def dyn(base, ts):
        d = ImageDraw.Draw(base)
        if n:
            k = min(1.0, max(0.0, (ts - 0.1) / 0.8))
            v = n[0] * (1 - (1 - k) ** 3)
            if k > 0:
                d.text((W / 2, yv), f"{n[2]}{num_fmt(v, n[1])}{n[3]}", font=fv, fill=ORANGE, anchor="mt")
        if b is not None:
            k = min(1.0, max(0.0, (ts - 0.25) / 0.9))
            if k > 0:
                rr(d, (130, by, W - 130, by + 60), 14, (80, 86, 90))
                fill = 130 + (W - 260) * b * (1 - (1 - k) ** 3)
                if fill > 140:
                    rr(d, (130, by, fill, by + 60), 14, ORANGE if k < 1 else (232, 150, 110))
    return {"parts": parts, "dyn": dyn}


def el_frage(text):
    """Überschrift mit [[Hervorhebung]] (petrol), Wörter erscheinen nacheinander."""
    f = font(76, "ExtraBold")
    plain = text.replace("[[", "").replace("]]", "")
    hl = {h.strip(".,!?") for h in " ".join(re.findall(r"\[\[(.+?)\]\]", text)).split()}
    d = ImageDraw.Draw(blank())
    lines = wrap(d, plain, f, 900)[:4]
    y = TOP_Y + 120
    parts, k = [], 0
    for ln in lines:
        x = (W - tw(d, ln, f)) / 2
        for wd in ln.split():
            lay = blank()
            ImageDraw.Draw(lay).text((x, y), wd, font=f, fill=(92, 200, 205) if wd.strip(".,!?") in hl else WHITE)
            parts.append(part(lay, k * 0.07, "pop"))
            x += tw(d, wd + " ", f)
            k += 1
        y += 92
    return {"parts": parts}


def build_element(el, prev=None):
    t = (el or {}).get("typ", "keins")
    if t == "hook":
        return el_hook(el.get("text", ""))
    if t == "chip":
        return el_chip(el.get("icon"), el.get("label", ""), el.get("wert", ""))
    if t == "ueberschrift":
        return el_label(el.get("text", ""))
    if t == "glow":
        return el_glow(el.get("icon"), el.get("titel", ""), el.get("unter", ""))
    if t == "tabelle":
        return el_tabelle(el.get("icon"), el.get("titel", ""), el.get("zeilen", [])[:5])
    if t == "liste":
        pk = el.get("punkte", [])[:5]
        alt = 0
        if (prev or {}).get("typ") == "liste" and prev.get("titel") == el.get("titel"):
            alt = min(len(prev.get("punkte", [])), len(pk))
        return el_liste(el.get("tag"), el.get("titel"), pk, alt)
    if t == "zahl":
        return el_zahl(el.get("oben", ""), el.get("wert", ""), el.get("balken"))
    if t == "frage":
        return el_frage(el.get("text", ""))
    return None


def ease_out(x):
    x = min(1.0, max(0.0, x))
    return 1 - (1 - x) ** 3


def ease_back(x):
    c1, c3 = 1.70158, 2.70158
    x = min(1.0, max(0.0, x))
    return 1 + c3 * (x - 1) ** 3 + c1 * (x - 1) ** 2


def draw_part(base, p, ts, static=False):
    if p is None:
        return
    k = 1.0 if (static or p.delay < 0) else (ts - p.delay) / 0.38
    if k <= 0:
        return
    if k >= 1:
        base.alpha_composite(p.img, (p.x, p.y))
        return
    img, x, y = p.img, p.x, p.y
    a = min(1.0, k * 2.0)
    if p.anim == "pop":
        s = 0.8 + 0.2 * ease_back(k)
        nw, nh = max(1, int(img.width * s)), max(1, int(img.height * s))
        img = img.resize((nw, nh), Image.BILINEAR)
        x, y = x + (p.img.width - nw) // 2, y + (p.img.height - nh) // 2
    elif p.anim == "up":
        y += int(60 * (1 - ease_out(k)))
    elif p.anim == "left":
        x -= int(90 * (1 - ease_out(k)))
    elif p.anim == "wipe":
        cw = max(1, int(img.width * ease_out(k)))
        img = img.crop((0, 0, cw, img.height))
        a = 1.0
    if a < 1:
        img = img.copy()
        img.putalpha(img.split()[3].point(lambda v: int(v * a)))
    base.alpha_composite(img, (max(0, x), max(0, y)))


# ---------------------------------------------------------------- Untertitel-Pille (Karaoke: aktuelles Wort grün)
def pill_frames(words):
    """Für einen Untertitel-Block je aktivem Wort ein fertiges Bild (W x 140)."""
    f = font(40)
    d = ImageDraw.Draw(blank())
    txt = [w[2] for w in words]
    gap = tw(d, " ", f)
    widths = [tw(d, t, f) for t in txt]
    total = sum(widths) + gap * (len(txt) - 1) + 90
    x0 = (W - total) / 2
    out = []
    for act in range(len(txt)):
        lay = Image.new("RGBA", (W, 140), (0, 0, 0, 0))
        dd = ImageDraw.Draw(lay)
        rr(dd, (x0, 30, x0 + total, 108), 18, (236, 237, 233))
        dd.ellipse((x0 + 26, 61, x0 + 42, 77), fill=RED)
        x = x0 + 58
        for i, t in enumerate(txt):
            dd.text((x, 45), t, font=f, fill=DARK)
            x += widths[i] + gap
        out.append(lay)
    return out


def halo():
    """Weicher grünlicher Lichtkreis hinter der Illustration."""
    s = 760
    yy, xx = np.mgrid[0:s, 0:s]
    r = np.sqrt((xx - s / 2) ** 2 + (yy - s / 2) ** 2) / (s / 2)
    a = np.clip(1 - r, 0, 1) ** 2 * 70
    img = np.zeros((s, s, 4), np.uint8)
    img[..., 0], img[..., 1], img[..., 2] = 70, 150, 120
    img[..., 3] = a.astype(np.uint8)
    return Image.fromarray(img, "RGBA")


# ---------------------------------------------------------------- Stimme
def tts(text, mp3):
    import edge_tts

    async def go():
        com = edge_tts.Communicate(text, VOICE, rate=RATE, boundary="WordBoundary")
        words = []
        with open(mp3, "wb") as fh:
            async for ch in com.stream():
                if ch["type"] == "audio":
                    fh.write(ch["data"])
                elif ch["type"] == "WordBoundary":
                    words.append((ch["offset"] / 1e7, (ch["offset"] + ch["duration"]) / 1e7, ch["text"]))
        return words

    for attempt in range(3):
        try:
            return asyncio.run(go())
        except Exception as e:  # noqa: BLE001
            print("  TTS-Fehler:", e)
    raise RuntimeError("edge-tts nicht erreichbar")


def norm(w):
    return re.sub(r"[^\wäöüß]", "", w.lower())


def align(scenes, words):
    """Ordnet jeder Szene Start/Ende und die gesprochenen Wörter (mit Zeiten) zu.
    Grobe Aufteilung nach Wortanteil, dann Szenengrenze auf das passende erste Wort verschieben
    (die Stimme spricht Zahlen oft als mehrere Wörter, daher kein starrer 1:1-Abgleich)."""
    toks = [[t for t in sc["say"].split() if norm(t)] for sc in scenes]
    total = sum(len(t) for t in toks) or 1
    n = len(words)
    bounds, cum = [0], 0
    for i, tk in enumerate(toks[:-1]):
        cum += len(tk)
        guess = round(cum / total * n)
        first = norm(toks[i + 1][0]) if toks[i + 1] else ""
        best = guess
        for d in range(0, 7):
            hit = [g for g in (guess - d, guess + d) if bounds[-1] < g < n and norm(words[g][2]) == first]
            if hit:
                best = hit[0]
                break
        bounds.append(max(bounds[-1], min(best, n)))
    bounds.append(n)
    for i, sc in enumerate(scenes):
        sc["words"] = list(words[bounds[i]:bounds[i + 1]])
    end = words[-1][1] if words else 2.0
    last = 0.0
    for i, sc in enumerate(scenes):
        sc["t0"] = 0.0 if i == 0 else (sc["words"][0][0] - 0.08 if sc["words"] else last)
        last = sc["words"][-1][1] if sc["words"] else last + 1.0
    for i, sc in enumerate(scenes):
        sc["t1"] = scenes[i + 1]["t0"] if i + 1 < len(scenes) else end + 0.9
        if sc["t1"] <= sc["t0"]:
            sc["t1"] = sc["t0"] + 0.5


def chunks(words, maxw=4, maxc=26):
    out, cur = [], []
    for w in words:
        if cur and (len(cur) >= maxw or len(" ".join(x[2] for x in cur + [w])) > maxc):
            out.append(cur)
            cur = []
        cur.append(w)
        if w[2][-1:] in ".,?!:":
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return [(c[0][0], c[-1][1], " ".join(x[2] for x in c)) for c in out]


# ---------------------------------------------------------------- Rendern
COVER_FRAMES = 3  # die ersten 0,1 s zeigen das Titelbild -> TikTok nimmt das erste Bild als Vorschaubild


def cover_image(data, bg, img):
    """Einheitliches Vorschaubild fürs Profil: Marke oben, Schlagzeile groß, Illustration, Marke unten.
    Alles Wichtige liegt im 3:4-Ausschnitt der Profil-Kachel (y 240–1680)."""
    fr = bg.copy()
    d = ImageDraw.Draw(fr)
    # Kopf: grüne Pille "PFLEGE-NEWS"
    fk = font(44, "ExtraBold")
    tag = "PFLEGE-NEWS"
    w = tw(d, tag, fk) + 70
    rr(d, ((W - w) / 2, 300, (W + w) / 2, 380), 40, GREEN)
    d.text((W / 2, 340), tag, font=fk, fill=WHITE, anchor="mm")
    # Schlagzeile (Hook aus Szene 1, sonst Thema)
    sc0 = (data.get("szenen") or [{}])[0]
    head = ((sc0.get("element") or {}).get("text") if (sc0.get("element") or {}).get("typ") == "hook" else None) or data.get("thema", "")
    head = head.replace("[[", "").replace("]]", "")
    f = font(92, "ExtraBold")
    lines = wrap(d, head, f, 900)[:4]
    lh = 112
    y0 = 450
    for i, ln in enumerate(lines):
        lw = tw(d, ln, f)
        x = (W - lw) / 2
        rr(d, (x - 24, y0 + i * lh - 6, x + lw + 24, y0 + i * lh + lh - 6), 12, WHITE)
        d.text((x, y0 + i * lh), ln, font=f, fill=DARK)
    # Illustration darunter
    if img is not None:
        top = y0 + len(lines) * lh + 30
        room = 1560 - top
        s = min(1.0, room / img.height)
        ii = img.resize((int(img.width * s), int(img.height * s)), Image.LANCZOS)
        fr.alpha_composite(ii, (int((W - ii.width) / 2), int(top)))
    # Fuß: Marke
    ff = font(46, "Bold")
    d.text((W / 2, 1610), "Pflege mit Plan", font=ff, fill=GREEN_T, anchor="mm")
    return fr


def render(data, wd):
    wd.mkdir(parents=True, exist_ok=True)
    scenes = data["szenen"]
    text = " ".join(s["say"].strip() for s in scenes)
    words = tts(text, wd / "voice.mp3")
    align(scenes, words)
    total = scenes[-1]["t1"] + 0.6
    bg = background()
    glow = halo()
    imgs, prev_img, prev_el = {}, None, None
    for i, sc in enumerate(scenes):
        b = (sc.get("bild") or "").strip()
        if b.lower() in ("", "keins", "none"):
            sc["_img"] = None
        elif b.lower() == "gleich":
            sc["_img"] = prev_img
        else:
            if b not in imgs:
                imgs[b] = fetch_image(b, wd / "img")
            sc["_img"] = imgs[b]
        prev_img = sc["_img"]
        el = sc.get("element")
        sc["_el"] = build_element(el, prev_el)
        sc["_static"] = bool(i > 0 and el and el == scenes[i - 1].get("element"))
        prev_el = el
        sc["_pills"] = []
        for a, z, _t in chunks(sc["words"]):
            ws = [w for w in sc["words"] if a <= w[0] <= z]
            if ws:
                sc["_pills"].append((a, z, ws, pill_frames(ws)))
    # Musik leise darunter
    music = wd / "music.wav"
    try:
        from video.ambient_music import build_music_loop
        build_music_loop(music, seed=data.get("thema", "pflege"), minutes=max(1.5, total / 60 + 0.2))
    except Exception as e:  # noqa: BLE001
        print("  Musik übersprungen:", e)
        music = None
    vid = wd / "video_silent.mp4"
    ff = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                           "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "medium",
                           "-crf", "20", "-pix_fmt", "yuv420p", str(vid)], stdin=subprocess.PIPE)
    n = int(total * FPS)
    first_img = next((s["_img"] for s in scenes if s["_img"] is not None), None)
    cov = cover_image(data, bg, first_img)
    cov.convert("RGB").save(wd / "cover.jpg", quality=92)
    cov_bytes = cov.convert("RGB").tobytes()
    # Lichtstreifen für Übergänge (schräg, weich)
    band = Image.new("RGBA", (520, H + 400), (0, 0, 0, 0))
    bx = np.abs(np.linspace(-1, 1, 520))
    ba = (np.clip(1 - bx, 0, 1) ** 2 * 46).astype(np.uint8)
    barr = np.zeros((H + 400, 520, 4), np.uint8)
    barr[..., 0], barr[..., 1], barr[..., 2] = 215, 240, 228
    barr[..., 3] = ba[None, :]
    band = Image.fromarray(barr, "RGBA").rotate(14, expand=True, resample=Image.BILINEAR)
    BIG = ("zahl", "tabelle", "liste")

    def shown(i):
        if i < 0:
            return None
        return None if (scenes[i].get("element") or {}).get("typ") in BIG else scenes[i]["_img"]

    def img_layer(im, z):
        fit = min(IMG_W / im.width, IMG_H / im.height)
        return im.resize((int(im.width * fit * z), int(im.height * fit * z)), Image.BILINEAR)

    def comp(base, img, x, y):
        x, y = int(x), int(y)
        l, t_ = max(0, -x), max(0, -y)
        r, b = min(img.width, W - x), min(img.height, H - y)
        if r > l and b > t_:
            base.alpha_composite(img.crop((l, t_, r, b)), (x + l, y + t_))

    def fade(img, a):
        img = img.copy()
        img.putalpha(img.split()[3].point(lambda v: int(v * a)))
        return img

    rnd = np.random.default_rng(7)
    dots = [(rnd.uniform(0, W), rnd.uniform(0, H), rnd.uniform(6, 22), rnd.uniform(2, 4.5), rnd.uniform(0, 6.3)) for _ in range(26)]
    si = 0
    for fi in range(n):
        if fi < COVER_FRAMES:
            ff.stdin.write(cov_bytes)
            continue
        t = fi / FPS
        while si + 1 < len(scenes) and t >= scenes[si + 1]["t0"]:
            si += 1
        sc = scenes[si]
        ts = t - sc["t0"]
        fr = bg.copy()
        d = ImageDraw.Draw(fr)
        # treibende Lichtpunkte im Hintergrund
        for x0, y0, sp, r, ph in dots:
            x = (x0 + math.sin(t * 0.5 + ph) * 30) % W
            y = (y0 - t * sp) % H
            c = 52 + int(14 * (0.5 + 0.5 * math.sin(t * 1.3 + ph)))
            d.ellipse((x - r, y - r, x + r, y + r), fill=(c, c + 6, c + 4))
        # Fortschrittsbalken oben
        d.rectangle((0, 0, int(W * min(1.0, t / total)), 7), fill=GREEN_T)
        typ = (sc.get("element") or {}).get("typ")
        im = shown(si)                    # große Karten stehen allein (kein Bild)
        prev_im = shown(si - 1)
        img_wechsel = prev_im is not None and prev_im is not im
        # Übergang 1: altes Bild gleitet nach links raus
        if img_wechsel and ts < 0.38:
            ko = ease_out(ts / 0.38)
            oi = fade(img_layer(prev_im, 1.0 - 0.08 * ko), 1 - ko)
            comp(fr, oi, (W - oi.width) / 2 - 280 * ko, min(IMG_CY, PILL_Y - 30 - oi.height / 2) - oi.height / 2)
        if im is not None:
            j = si
            while j > 0 and shown(j - 1) is im:
                j -= 1
            tz = t - scenes[j]["t0"]          # Zeit seit dieses Bild erschien
            if j > 0 and shown(j - 1) is not None:
                tz -= 0.18                    # erst raus, dann rein
            if tz < 0:
                tz = 0.0
                k = 0.0
            else:
                k = ease_out(tz / 0.55)
            z = min(1.06, 1.0 + 0.012 * tz) * (0.9 + 0.1 * k)
            fit = min(IMG_W / im.width, IMG_H / im.height)
            sw, sh = int(im.width * fit * z), int(im.height * fit * z)
            ii = im.resize((sw, sh), Image.BILINEAR)
            cy = IMG_CY + 70 * (1 - k) + 10 * math.sin(tz * 1.25)
            cy = min(cy, PILL_Y - 30 - sh / 2)
            gs = 1.0 + 0.04 * math.sin(tz * 1.6)
            gg = glow.resize((int(glow.width * gs * z), int(glow.height * gs * z)), Image.BILINEAR)
            if k < 1:
                ii.putalpha(ii.split()[3].point(lambda v: int(v * k)))
                gg.putalpha(gg.split()[3].point(lambda v: int(v * k)))
            dx = int(240 * (1 - k)) if (j > 0 and shown(j - 1) is not None) else 0   # neues Bild kommt von rechts
            comp(fr, gg, (W - gg.width) / 2 + dx, cy - gg.height / 2)
            comp(fr, ii, (W - sw) / 2 + dx, cy - sh / 2)
        # Übergang 2: altes Element verabschiedet sich nach oben, neues kommt kurz danach
        prev_el = scenes[si - 1]["_el"] if si > 0 else None
        pe, ce = (scenes[si - 1].get("element") or {}) if si > 0 else {}, (sc.get("element") or {})
        wachsende_liste = pe.get("typ") == "liste" and ce.get("typ") == "liste" and pe.get("titel") == ce.get("titel")
        el_wechsel = si > 0 and not sc["_static"] and prev_el is not None and not wachsende_liste
        if el_wechsel and ts < 0.28:
            ko = ease_out(ts / 0.28)
            for p in prev_el["parts"]:
                if p is not None:
                    comp(fr, fade(p.img, 1 - ko), p.x, p.y - 60 * ko)
        te = ts - (0.16 if el_wechsel else 0.0)
        if sc["_el"] is not None:
            for p in sc["_el"]["parts"]:
                draw_part(fr, p, te, sc["_static"])
            if sc["_el"].get("dyn") and (sc["_static"] or te > 0):
                sc["_el"]["dyn"](fr, 9.0 if sc["_static"] else te)
        # Übergang 3: Lichtstreifen wischt über das Bild, wenn Grafik oder Karte wechselt
        if si > 0 and (img_wechsel or (im is not None and prev_im is None) or el_wechsel and typ in BIG) and ts < 0.5:
            bxp = int(-band.width + (ts / 0.5) * (W + band.width))
            comp(fr, band, bxp, -(band.height - H) // 2)
        for a, z, ws, frames in sc["_pills"]:
            if a - 0.05 <= t < z + 0.25:
                act = max(0, sum(1 for w in ws if w[0] <= t) - 1)
                p = frames[min(act, len(frames) - 1)]
                kk = ease_back((t - a + 0.05) / 0.18)
                if kk < 1:
                    s = 0.9 + 0.1 * kk
                    pw, ph = int(W * s), int(140 * s)
                    fr.alpha_composite(p.resize((pw, ph), Image.BILINEAR), ((W - pw) // 2, PILL_Y + (140 - ph) // 2))
                else:
                    fr.alpha_composite(p, (0, PILL_Y))
                break
        ff.stdin.write(fr.convert("RGB").tobytes())
    ff.stdin.close()
    ff.wait()
    out = wd / "video.mp4"
    inputs = ["-i", str(vid), "-i", str(wd / "voice.mp3")]
    if music and music.exists():
        inputs += ["-stream_loop", "-1", "-i", str(music)]
        af = "[1:a]aformat=sample_rates=44100:channel_layouts=stereo,volume=1.0[v];[2:a]aformat=sample_rates=44100:channel_layouts=stereo,volume=0.10[m];[v][m]amix=inputs=2:duration=first:normalize=0[a]"
    else:
        af = "[1:a]aformat=sample_rates=44100:channel_layouts=stereo[a]"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", af, "-map", "0:v", "-map", "[a]",
                    "-t", f"{total:.2f}", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)], check=True)
    return out, total


# ---------------------------------------------------------------- Skript (Claude + Websuche)
SYSTEM = """Du schreibst Skripte für kurze TikTok-Videos des Kanals „Pflege mit Plan" (Pflege-News für Pflegekräfte und Angehörige, Deutschland).
Stil wie ein ruhiger Nachrichten-Erklärer: kurze Sätze, Spannung, sachlich, kein Clickbait, das Video endet mit einer Frage an die Zuschauer.
WICHTIG: Nur echte, belegte Fakten aus den Quellen deiner Websuche. Hauptquelle muss seriös sein: Behörden/Ministerien, Krankenkassen, Pflegekassen, Verbände, Gerichte, Fachmedien (z. B. Ärzteblatt, CAREkonkret, Altenheim) oder große Nachrichtenmedien (tagesschau, Spiegel, Zeit, FAZ, SZ). Keine Ratgeber-Blogs, Foren oder Portale wie gegen-hartz.de oder buerger-geld.org – auch nicht als Umweg („via“): quelle_url muss direkt auf die seriöse Quelle zeigen. Keine erfundenen Zahlen, Namen oder Zitate. Keine Patientendaten, keine Hetze gegen Berufsgruppen.
Antworte am Ende ausschließlich mit einem JSON-Objekt (ohne Markdown)."""

FORMAT = """JSON-Format:
{
 "thema": "kurzer Titel",
 "quelle": "Name der Quelle, z. B. AOK Nordwest oder Ärzteblatt",
 "quelle_url": "https://...",
 "caption": "TikTok-Text: 1-2 Sätze mit Hauptsuchwort, 1 Emoji, eine Frage. Ohne Hashtags.",
 "hashtags": ["#pflege", "... 4 weitere passende"],
 "szenen": [ {"say": "Ein gesprochener Satz (max. 14 Wörter).", "bild": "...", "element": {...}} ]
}
Regeln für "szenen" (14-22 Stück, zusammen 140-190 Wörter, ca. 60-80 Sekunden):
- Szene 1 = Hook (überraschende Aussage), element {"typ":"hook","text":"Schlagzeile, max. 8 Wörter"}.
- "bild": englische Beschreibung EINER isometrischen Szene (Personen, Gebäude, Gegenstände), z. B. "three small houses with red roofs, a nurse in blue scrubs at each door". "gleich" = Bild der vorigen Szene behalten (für 2-3 Szenen hintereinander gut). "keins" bei Tabellen, Listen und großen Zahlen. Höchstens 6 verschiedene Bilder pro Video. Keine Schrift im Bild.
- "element" (passend zum Satz, Text deutsch, kurz):
  {"typ":"keins"}
  {"typ":"chip","icon":"🕗","label":"Uhrzeit","wert":"08:15"}            (kleine Infokarte oben)
  {"typ":"ueberschrift","text":"3× gleichzeitig?!"}                        (weiße Zeile über dem Bild)
  {"typ":"glow","icon":"✨","titel":"BEAMEN","unter":"So nennen es die Ermittler."}  (Schlagwort leuchtend)
  {"typ":"tabelle","icon":"📋","titel":"Leistungsnachweis","zeilen":[["08:15","🏠 Haus A"],["08:15","🏠 Haus B"]]}
  {"typ":"liste","tag":"laut <Quelle>","titel":"Und das ist nicht alles:","punkte":[{"icon":"✨","text":"Beamen"}]}  (wächst: in Folgeszenen dieselbe Liste mit einem Punkt mehr)
  {"typ":"zahl","oben":"zurückgeholt in 2 Jahren:","wert":"7,5 Mio. €","balken":0.4}
  {"typ":"frage","text":"Den Preis zahlen die [[Ehrlichen]]."}
- PFLICHT: Mindestens 8 Szenen mit sichtbarem Element außer "keins" (chip, ueberschrift, glow, tabelle, liste, zahl, frage), davon mindestens 2 verschiedene Kartentypen aus chip/tabelle/liste/zahl. Jede genannte Zahl wird als "zahl" oder "chip" gezeigt. Höchstens 5 Szenen mit "keins".
- Ein Element darf über mehrere Szenen gleich bleiben (identisches Objekt wiederholen).
- Letzte Szene: Frage an die Zuschauer + „Schreib es in die Kommentare." mit element frage.
- Zahlen im "say" so schreiben, wie sie gesprochen werden (z. B. "siebeneinhalb Millionen Euro")."""


def script(avoid):
    import anthropic
    client = anthropic.Anthropic()
    today = dt.datetime.now(TZ).strftime("%d.%m.%Y")
    user = (f"Heute ist der {today}. Suche eine aktuelle, belegte Pflege-Nachricht aus Deutschland (letzte 30 Tage) mit "
            "Überraschungseffekt: z. B. Abrechnungsbetrug, neue Regeln, Pflegegeld, Personalmangel, Gerichtsurteile, Studien. "
            f"Diese Themen gab es schon (nicht wiederholen): {avoid or 'keine'}.\n\n{FORMAT}")
    # Schritt 1: Recherche mit Websuche -> Faktenblatt (Text über evtl. mehrere pause_turn-Runden sammeln)
    research = (f"Heute ist der {today}. Suche eine aktuelle, belegte Pflege-Nachricht aus Deutschland (letzte 30 Tage) mit "
                "Überraschungseffekt: z. B. Abrechnungsbetrug, neue Regeln, Pflegegeld, Personalmangel, Gerichtsurteile, Studien. "
                f"Diese Themen gab es schon (nicht wiederholen): {avoid or 'keine'}.\n"
                "Gib als Ergebnis ein kurzes Faktenblatt aus: Thema, Hauptquelle (Name + URL), 6-12 belegte Fakten mit Zahlen. Noch kein Skript.")
    msgs = [{"role": "user", "content": research}]
    parts = []
    for _ in range(5):
        resp = client.messages.create(model=NEWS_MODEL, max_tokens=8000, system=SYSTEM.rsplit("\n", 1)[0],
                                      tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}],
                                      messages=msgs)
        if resp.stop_reason == "refusal":
            raise RuntimeError("Recherche abgelehnt")
        parts += [b.text for b in resp.content if b.type == "text"]
        if resp.stop_reason == "pause_turn":
            msgs = msgs + [{"role": "assistant", "content": resp.content}]
            continue
        break
    facts = "".join(parts).strip()
    if len(facts) < 200:
        raise RuntimeError(f"Recherche zu dünn: {facts[:200]}")
    # Schritt 2: Skript aus dem Faktenblatt (ohne Werkzeuge, nur JSON)
    last = ""
    for attempt in range(2):
        resp = client.messages.create(model=NEWS_MODEL, max_tokens=12000, system=SYSTEM, messages=[{
            "role": "user", "content": f"Faktenblatt (nur diese Fakten verwenden):\n{facts}\n\n{FORMAT}\n\nAntworte nur mit dem JSON-Objekt."}])
        if resp.stop_reason == "refusal":
            raise RuntimeError("Skript abgelehnt")
        last = "".join(b.text for b in resp.content if b.type == "text")
        i, j = last.find("{"), last.rfind("}")
        try:
            data = json.loads(last[i:j + 1])
            if data.get("szenen"):
                return data
        except Exception as e:  # noqa: BLE001
            print("  JSON-Fehler, neuer Versuch:", e)
    raise RuntimeError(f"kein gültiges JSON: {last[-300:]}")


# ---------------------------------------------------------------- Upload
def queue(path, data, secs):
    tok = os.environ.get("PFLEGE_UPLOAD_TOKEN", "").strip()
    if not tok:
        print("  kein PFLEGE_UPLOAD_TOKEN, nicht eingereiht")
        return None
    h = {"x-gaming-token": tok, "Content-Type": "application/json"}
    name = re.sub(r"[^\w\-]", "_", f"pflege-news-{dt.datetime.now(TZ):%H%M%S}-{data['thema']}")[:70] + ".mp4"
    r = requests.post(GAMING_UPLOAD, json={"action": "upload_url", "name": name}, headers=h, timeout=60).json()
    if "signed_url" not in r:
        raise RuntimeError(f"upload_url: {r}")
    with open(path, "rb") as f:
        up = requests.put(r["signed_url"], data=f, headers={"Content-Type": "video/mp4", "x-upsert": "true"}, timeout=300)
    if up.status_code >= 300:
        raise RuntimeError(f"Upload {up.status_code}: {up.text[:200]}")
    tags = " ".join(data.get("hashtags") or ["#pflege", "#pflegekraft", "#pflegenews"])
    caption = f"{data.get('caption', data['thema']).strip()}\nQuelle: {data.get('quelle', '')}\n{tags}"
    reg = requests.post(GAMING_UPLOAD, json={"action": "register", "path": r["path"], "title": data["thema"][:120],
                                              "caption": caption, "seconds": round(secs, 1), "source": "Pflege-News (tiktok_news)"},
                        headers=h, timeout=60).json()
    print("  eingereiht:", reg)
    return reg


YT_SLOTS = ["12:30", "18:30"]   # wie bisher die Pflege-Shorts auf YouTube
SITE = "https://pflege-mit-plan.com"


def youtube_short(path, data, slot):
    """Dasselbe Video wie auf TikTok als YouTube-Short auf „Pflege mit Plan“ (Robin 23.11.2026)."""
    if not Path(ROOT / "token.json").exists() and not Path("token.json").exists():
        print("  kein YouTube-Token (token.json) – kein YouTube-Upload")
        return None
    from upload.upload_video import upload as yt_upload
    title = re.sub(r"\s+", " ", data.get("thema", "Pflege-News")).strip()[:85] + " #Shorts"
    tags = [t.lstrip("#") for t in (data.get("hashtags") or [])][:10] + ["Pflege", "Pflege mit Plan", "Pflegenews"]
    desc = "\n".join([
        data.get("caption", data.get("thema", "")).strip(), "",
        f"Quelle: {data.get('quelle', '')} {data.get('quelle_url', '')}".strip(),
        f"👉 Mehr Hilfe für pflegende Angehörige: {SITE}",
        *([f"🎁 Geschenkideen für Senioren & pflegende Angehörige: {SITE}/geschenkideen-senioren-pflege"]
          if dt.date.today() <= dt.date(dt.date.today().year, 12, 24) and dt.date.today().month >= 11 else []),
        f"📖 Mein Pflegetagebuch zum Ausfüllen: https://www.amazon.de/dp/B0HR97VS62?tag={config.AMAZON_TAG_PFLEGE_YT}",
        f"🆘 Meine Pflege-Notfallmappe: https://www.amazon.de/dp/B0HRKB47NW?tag={config.AMAZON_TAG_PFLEGE_YT}",
        "(Werbung: Als Amazon-Partner verdiene ich an qualifizierten Verkäufen.)", "",
        "Keine Rechts- oder medizinische Beratung.", "",
        " ".join((data.get("hashtags") or ["#pflege"])[:5]) + " #PflegeMitPlan #Shorts",
    ])
    review = os.getenv("PFLEGE_REVIEW", "1") == "1"
    h, m = map(int, slot.split(":"))
    local = dt.datetime.combine(dt.datetime.now(TZ).date(), dt.time(h, m), TZ)
    when = None if local < dt.datetime.now(TZ) + dt.timedelta(minutes=20) else local.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if review:
        return yt_upload(str(path), title, desc, list(dict.fromkeys(tags)), privacy_status="private")
    return yt_upload(str(path), title, desc, list(dict.fromkeys(tags)), publish_at=when, privacy_status=None if when else "public")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--script", default="")
    a = ap.parse_args()
    import projekt
    tt_on, yt_on = projekt.aktiv("tt-pflege"), projekt.aktiv("yt-pflege")
    if not a.test and not a.script and not tt_on and not yt_on:
        print("Kanal-Schalter: Pflege mit Plan (TikTok + YouTube) ruht – nichts zu tun.")
        return
    st = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {"themen": []}
    day = dt.datetime.now(TZ).date().isoformat()
    ok = 0
    for i in range(1 if (a.test or a.script) else a.count):
        try:
            data = json.loads(Path(a.script).read_text(encoding="utf-8")) if a.script else script([x["thema"] for x in st["themen"][-40:]])
            print(f"[{i}] {data['thema']} ({data.get('quelle')})")
            wd = OUT / day / f"{i}"
            (wd).mkdir(parents=True, exist_ok=True)
            (wd / "script.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            path, secs = render(data, wd)
            print(f"  gerendert: {path} ({secs:.0f} s)")
            if not a.test and not a.script:
                if tt_on:
                    queue(path, data, secs)
                else:
                    print("  Kanal-Schalter: tt-pflege ruht – nicht eingereiht.")
                try:
                    vid = youtube_short(path, data, YT_SLOTS[ok % len(YT_SLOTS)]) if yt_on else "übersprungen (yt-pflege ruht)"
                    print("  YouTube-Short:", vid)
                except Exception as e:  # noqa: BLE001
                    print("  YouTube FEHLER:", str(e)[:300])
                st["themen"].append({"thema": data["thema"], "url": data.get("quelle_url"), "datum": day})
                st["themen"] = st["themen"][-200:]
                STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
            ok += 1
        except Exception as e:  # noqa: BLE001
            print("  FEHLER:", str(e)[:400])
    if ok == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
