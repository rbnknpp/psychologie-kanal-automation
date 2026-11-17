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
RATE = os.getenv("PFLEGE_TT_RATE", "+6%")
BILDER = "https://kfiiaqjzzwwacthzrcut.supabase.co/functions/v1/site-bilder"
GAMING_UPLOAD = "https://kfiiaqjzzwwacthzrcut.supabase.co/functions/v1/gaming-upload"
STYLE = ("isometric 3D illustration, clean low-poly diorama, soft studio lighting, muted colors, "
         "isolated on plain flat dark charcoal background, no text, no letters, no logos, centered composition")

BG_TOP, BG_BOT = (36, 39, 42), (22, 24, 26)
WHITE, DARK, MUTED = (245, 245, 243), (30, 33, 36), (150, 156, 160)
GREEN, GREEN_T = (46, 107, 85), (215, 240, 228)
ORANGE, RED, TEAL = (232, 128, 74), (214, 64, 64), (92, 214, 214)
CARD = (238, 239, 236)

IMG_W, IMG_H = 900, 760          # Fläche für die Illustration
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


# ---------------------------------------------------------------- Elemente (RGBA-Ebenen, unanimiert)
def el_hook(text):
    lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    f = font(78)
    lines = wrap(d, text, f, 880)
    lh = 96
    y0 = 760 - len(lines) * lh / 2
    for i, ln in enumerate(lines):
        w = tw(d, ln, f)
        x = (W - w) / 2
        rr(d, (x - 22, y0 + i * lh - 8, x + w + 22, y0 + i * lh + lh - 4), 10, WHITE)
        d.text((x, y0 + i * lh), ln, font=f, fill=DARK)
    return lay, (W / 2, 760)


def el_chip(icon, label, wert):
    lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    fl, fv = font(34, "SemiBold"), font(86)
    w = max(tw(d, wert, fv), tw(d, label, fl) + 60) + 50
    x0, y0 = (W - w) / 2, 150
    rr(d, (x0, y0, x0 + w, y0 + 200), 22, CARD)
    d.rounded_rectangle((x0, y0, x0 + w, y0 + 64), radius=22, fill=GREEN, corners=(True, True, False, False))
    e = emoji(icon, 34) if icon else None
    lx = x0 + 22
    if e:
        lay.alpha_composite(e, (int(lx), int(y0 + 15)))
        lx += 46
    d.text((lx, y0 + 12), label, font=fl, fill=WHITE)
    d.text((x0 + 25, y0 + 78), wert, font=fv, fill=DARK)
    return shadowed(lay), (W / 2, y0 + 100)


def el_label(text):
    lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    f = font(64)
    for i, ln in enumerate(wrap(d, text, f, 900)):
        d.text((W / 2, 440 + i * 78), ln, font=f, fill=WHITE, anchor="mt")
    return lay, (W / 2, 470)


def el_glow(icon, titel, unter):
    lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    f = font(110, "ExtraBold") if True else None
    e = emoji(icon, 96) if icon else None
    tw_ = tw(d, titel, f) + (116 if e else 0)
    x = (W - tw_) / 2
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.text((x + (116 if e else 0), 300), titel, font=f, fill=TEAL + (255,))
    glow = glow.filter(ImageFilter.GaussianBlur(18))
    lay.alpha_composite(glow)
    lay.alpha_composite(glow)
    if e:
        lay.alpha_composite(e, (int(x), 310))
    d.text((x + (116 if e else 0), 300), titel, font=f, fill=(150, 235, 240))
    if unter:
        d.text((W / 2, 450), unter, font=font(40, "SemiBold"), fill=WHITE, anchor="mt")
    return lay, (W / 2, 380)


def el_tabelle(icon, titel, zeilen):
    lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    fh, fr = font(40), font(40, "SemiBold")
    x0, y0, w = 90, 150, 900
    h = 90 + 76 * len(zeilen) + 20
    rr(d, (x0, y0, x0 + w, y0 + h), 22, CARD)
    e = emoji(icon, 40) if icon else None
    tx = x0 + 30
    if e:
        lay.alpha_composite(e, (tx, y0 + 26))
        tx += 54
    d.text((tx, y0 + 24), titel, font=fh, fill=DARK)
    for i, z in enumerate(zeilen):
        y = y0 + 96 + i * 76
        d.line((x0 + 24, y - 8, x0 + w - 24, y - 8), fill=(214, 216, 212), width=2)
        a = str(z[0]) if len(z) > 0 else ""
        b = str(z[1]) if len(z) > 1 else ""
        etext(lay, (x0 + 40, y + 6), a, fr, RED, 38)
        etext(lay, (x0 + 330, y + 6), b, fr, DARK, 38)
        d.text((x0 + w - 70, y + 4), "✓", font=font(44), fill=GREEN)
    return shadowed(lay), (W / 2, y0 + h / 2)


def el_liste(tag, titel, punkte, upto):
    lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    y = 160
    if tag:
        ft = font(32, "SemiBold")
        t = "📰 " + tag
        w = tw(d, tag, ft) + 90
        rr(d, ((W - w) / 2, y, (W + w) / 2, y + 58), 29, GREEN)
        e = emoji("📰", 30)
        if e:
            lay.alpha_composite(e, (int((W - w) / 2 + 24), y + 14))
        d.text(((W - w) / 2 + 66, y + 10), tag, font=ft, fill=GREEN_T)
        y += 100
    if titel:
        d.text((W / 2, y), titel, font=font(54), fill=WHITE, anchor="mt")
        y += 100
    boxes = []
    for i, p in enumerate(punkte[:upto]):
        bx = (70, y, W - 70, y + 104)
        rr(d, bx, 18, CARD)
        e = emoji(p.get("icon") or "•", 52) if p.get("icon") else None
        if e:
            lay.alpha_composite(e, (100, int(y + 26)))
        d.text((180, y + 26), EMOJI_RE.sub("", str(p.get("text", "")))[:38], font=font(44, "SemiBold"), fill=DARK)
        boxes.append(bx)
        y += 128
    last = boxes[-1] if boxes else (0, 600, W, 700)
    return shadowed(lay), (W / 2, (last[1] + last[3]) / 2)


def el_zahl(oben, wert, balken):
    lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    if oben:
        d.text((W / 2, 520), oben, font=font(44), fill=WHITE, anchor="mt")
    d.text((W / 2, 640), wert, font=font(150, "ExtraBold"), fill=ORANGE, anchor="mt")
    if balken is not None:
        b = max(0.03, min(1.0, float(balken)))
        rr(d, (130, 860, W - 130, 920), 12, (80, 86, 90))
        rr(d, (130, 860, 130 + (W - 260) * b, 920), 12, (170, 176, 180))
    return lay, (W / 2, 720)


def el_frage(text):
    """Überschrift mit [[Hervorhebung]] (petrol)."""
    lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    f = font(74)
    plain = text.replace("[[", "").replace("]]", "")
    hl = set(" ".join(re.findall(r"\[\[(.+?)\]\]", text)).split())
    lines = wrap(d, plain, f, 900)
    y = 230
    for ln in lines:
        words = ln.split()
        total = tw(d, ln, f)
        x = (W - total) / 2
        for wd in words:
            col = (72, 160, 170) if wd.strip(".,!?") in {h.strip(".,!?") for h in hl} else WHITE
            d.text((x, y), wd, font=f, fill=col)
            x += tw(d, wd + " ", f)
        y += 90
    return lay, (W / 2, 300)


def build_element(el, state):
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
        return el_liste(el.get("tag"), el.get("titel"), pk, len(pk))
    if t == "zahl":
        return el_zahl(el.get("oben", ""), el.get("wert", ""), el.get("balken"))
    if t == "frage":
        return el_frage(el.get("text", ""))
    return None


# ---------------------------------------------------------------- Untertitel-Pille
def pill(text):
    lay = Image.new("RGBA", (W, 140), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    f = font(38)
    w = tw(d, text, f) + 90
    x0 = (W - w) / 2
    rr(d, (x0, 30, x0 + w, 106), 16, (236, 237, 233))
    d.ellipse((x0 + 26, 60, x0 + 42, 76), fill=RED)
    d.text((x0 + 58, 44), text, font=f, fill=DARK)
    return lay


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
def ease_back(x):
    c1, c3 = 1.70158, 2.70158
    x = min(1.0, max(0.0, x))
    return 1 + c3 * (x - 1) ** 3 + c1 * (x - 1) ** 2


def place(base, layer, anchor, k):
    """Element mit Pop-in (Skalierung um anchor, Deckkraft)."""
    if k >= 1:
        base.alpha_composite(layer)
        return
    s = 0.86 + 0.14 * ease_back(k)
    a = min(1.0, k * 2.2)
    sw, sh = int(W * s), int(H * s)
    sm = layer.resize((sw, sh), Image.BILINEAR)
    if a < 1:
        al = sm.split()[3].point(lambda v: int(v * a))
        sm.putalpha(al)
    ax, ay = anchor
    ox, oy = int(ax - ax * s), int(ay - ay * s)
    base.alpha_composite(sm, (max(0, ox), max(0, oy)) if ox >= 0 and oy >= 0 else (0, 0))


def render(data, wd):
    wd.mkdir(parents=True, exist_ok=True)
    scenes = data["szenen"]
    text = " ".join(s["say"].strip() for s in scenes)
    words = tts(text, wd / "voice.mp3")
    align(scenes, words)
    total = scenes[-1]["t1"] + 0.6
    bg = background()
    imgs, prev_img = {}, None
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
        sc["_el"] = build_element(sc.get("element"), None)
        sc["_pills"] = [(a, z, pill(t)) for a, z, t in chunks(sc["words"])]
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
    si = 0
    for fi in range(n):
        t = fi / FPS
        while si + 1 < len(scenes) and t >= scenes[si + 1]["t0"]:
            si += 1
        sc = scenes[si]
        ts = t - sc["t0"]
        fr = bg.copy()
        im = sc["_img"]
        if im is not None:
            same = si > 0 and scenes[si - 1]["_img"] is im
            # gleichmäßiger langsamer Zoom über alle Szenen mit demselben Bild
            j = si
            while j > 0 and scenes[j - 1]["_img"] is im:
                j -= 1
            tz = t - scenes[j]["t0"]
            z = 1.0 + 0.010 * tz
            sw, sh = int(IMG_W * z), int(IMG_H * z)
            ii = im.resize((sw, sh), Image.BILINEAR)
            typ = (sc.get("element") or {}).get("typ")
            # Bildmitte: unter Karten/Überschriften etwas tiefer, unten immer über der Pille enden
            cy = 930 if typ in ("chip", "ueberschrift", "glow", "frage", "tabelle", "liste") else 880
            cy = min(cy, PILL_Y - 20 - sh / 2)
            k = 1.0 if same else min(1.0, ts / 0.25)
            if k < 1:
                ii.putalpha(ii.split()[3].point(lambda v: int(v * k)))
            fr.alpha_composite(ii, (int((W - sw) / 2), int(cy - sh / 2)))
        if sc["_el"] is not None:
            lay, anchor = sc["_el"]
            prev_same = si > 0 and scenes[si - 1].get("element") == sc.get("element")
            place(fr, lay, anchor, 1.0 if prev_same else ts / 0.32)
        for a, z, p in sc["_pills"]:
            if a - 0.05 <= t < z + 0.25:
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
WICHTIG: Nur echte, belegte Fakten aus den Quellen deiner Websuche. Hauptquelle muss seriös sein: Behörden/Ministerien, Krankenkassen, Pflegekassen, Verbände, Gerichte, Fachmedien (z. B. Ärzteblatt, CAREkonkret, Altenheim) oder große Nachrichtenmedien (tagesschau, Spiegel, Zeit, FAZ, SZ). Keine Ratgeber-Blogs, Foren oder Portale wie gegen-hartz.de. Keine erfundenen Zahlen, Namen oder Zitate. Keine Patientendaten, keine Hetze gegen Berufsgruppen.
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
        resp = client.messages.create(model=config.ANTHROPIC_MODEL, max_tokens=8000, system=SYSTEM.rsplit("\n", 1)[0],
                                      tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 6}],
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
        resp = client.messages.create(model=config.ANTHROPIC_MODEL, max_tokens=12000, system=SYSTEM, messages=[{
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--script", default="")
    a = ap.parse_args()
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
                queue(path, data, secs)
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
