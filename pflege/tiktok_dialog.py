"""Pflege-Dialogvideos (seit 25.11.2026, Stil nach Robins Vorlage aus Dropbox – eigene Marke „Pflege mit Plan“).

Eine kleine Alltagsszene mit festen Figuren (Mutter Hilde, Tochter Anja, Pflegerin Jana, Pflegeberater Herr Becker),
jede mit eigener Stimme (edge-tts). Bild = feste 3D-Szenen aus assets/pflege_dialog (gleiche Figuren in jedem Video),
langsame Kamerafahrt, Schnitt bei jedem Sprecherwechsel. Oben Zahlen-Karten (Kassenbon, Bausteine, Vergleich),
unten Untertitel mit Namensschild. Fakten nur von der passenden Seite auf pflege-mit-plan.com.

Aufruf:  python -m pflege.tiktok_dialog                  (Tagesplan: 1 News, 1 Angehörige, 1 Azubi -> TikTok-Entwurf + YouTube-Short)
         python -m pflege.tiktok_dialog --plan azubi      (nur diese Reihe(n))
         python -m pflege.tiktok_dialog --test            (1 Video, nur rendern)
         python -m pflege.tiktok_dialog --script x.json   (vorhandenes Skript rendern)
"""
import argparse
import asyncio
import datetime as dt
import json
import math
import os
import random
import re
import subprocess
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pflege.tiktok_news import font, wrap, tw, rr, queue, youtube_short, ease_out, ease_back  # noqa: E402
from pflege.topics import ANGEHOERIGE, AZUBI, SITE  # noqa: E402

HERE = Path(__file__).resolve().parent
ASSETS = ROOT / "assets" / "pflege_dialog"
STATE = HERE / "dialog_state.json"
OUT = ROOT / "output" / "pflege_dialog"
TZ = ZoneInfo("Europe/Berlin")
W, H, FPS = 1080, 1920, 30
MODEL = os.getenv("PFLEGE_DIALOG_MODEL", "claude-sonnet-5-5")

# Marke „Pflege mit Plan“: Grün + Sand + Orange-Akzent
GREEN, GREEN_D, SAND, ORANGE = (46, 107, 85), (24, 64, 50), (247, 242, 234), (224, 123, 57)
INK, WHITE, MUTED = (28, 31, 30), (255, 255, 255), (110, 116, 112)

# Besetzung: Stimme, Namensschild, Farbe des Schilds, Standard-Einstellung
CAST = {
    "hilde": {"name": "HILDE · MUTTER", "voice": "de-DE-AmalaNeural", "rate": "-12%", "pitch": "-14Hz", "col": (196, 160, 112), "shot": "hilde_nah"},
    "anja": {"name": "ANJA · TOCHTER", "voice": "de-DE-KatjaNeural", "rate": "+4%", "pitch": "+0Hz", "col": (226, 170, 60), "shot": "anja_nah"},
    "jana": {"name": "JANA · PFLEGEDIENST", "voice": "de-DE-SeraphinaMultilingualNeural", "rate": "+4%", "pitch": "+0Hz", "col": (70, 170, 150), "shot": "jana_nah"},
    "becker": {"name": "HERR BECKER · PFLEGEBERATUNG", "voice": "de-DE-FlorianMultilingualNeural", "rate": "+2%", "pitch": "+0Hz", "col": (110, 140, 200), "shot": "becker_nah"},
    "leon": {"name": "LEON · AZUBI", "voice": "de-DE-ChristophNeural", "rate": "+6%", "pitch": "+0Hz", "col": (150, 190, 235), "shot": "leon_nah"},
}
SHOTS = {
    "wohnzimmer_alle": "Wohnzimmer, alle drei (Hilde im Sessel, Anja dahinter, Jana auf dem Sofa)",
    "hilde_sessel": "Hilde im Sessel (halbnah)", "hilde_nah": "Hilde nah, lächelt", "hilde_skeptisch": "Hilde nah, skeptisch",
    "anja_nah": "Anja nah, besorgt", "anja_halb": "Anja halbnah am Sessel, erklärt", "anja_tisch_brief": "Anja am Küchentisch mit Brief",
    "anja_telefon": "Anja telefoniert im Flur",
    "jana_nah": "Jana nah, erklärt", "jana_sofa": "Jana auf dem Sofa mit Klemmbrett", "jana_auto": "Jana fährt im Regen Auto",
    "jana_tuer": "Jana klingelt an der Haustür",
    "becker_nah": "Herr Becker nah", "becker_buero": "Herr Becker im Büro mit Anja",
    "kueche_alle": "Hilde und Anja am Küchentisch mit Kaffee", "bad": "Barrierefreies Bad (leer)",
    "leon_nah": "Leon nah, fragt nach", "leon_lernt": "Leon lernt abends am Schreibtisch", "schule_klasse": "Leon im Klassenraum der Pflegeschule",
    "jana_tafel": "Jana erklärt am Whiteboard", "skills_lab": "Jana zeigt Leon etwas im Übungsraum an der Puppe im Pflegebett",
    "station_flur": "Jana und Leon gehen über den Stationsflur",
}

MONO = next((p for p in ["/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf", r"C:\Windows\Fonts\consolab.ttf"] if Path(p).exists()), None)
_mono = {}


def mono(size):
    if size not in _mono:
        _mono[size] = ImageFont.truetype(MONO, size) if MONO else font(size, "Bold")
    return _mono[size]


# ---------------------------------------------------------------- Stimme
def tts_line(text, voice, rate, pitch, mp3):
    import edge_tts

    async def go():
        com = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch, boundary="WordBoundary")
        words = []
        with open(mp3, "wb") as fh:
            async for ch in com.stream():
                if ch["type"] == "audio":
                    fh.write(ch["data"])
                elif ch["type"] == "WordBoundary":
                    words.append((ch["offset"] / 1e7, (ch["offset"] + ch["duration"]) / 1e7, ch["text"]))
        return words

    for _ in range(3):
        try:
            return asyncio.run(go())
        except Exception as e:  # noqa: BLE001
            print("  TTS-Fehler:", e)
    raise RuntimeError("edge-tts nicht erreichbar")


def pcm(mp3, sr=24000):
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(mp3), "-f", "s16le", "-ac", "1", "-ar", str(sr), "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.int16)


def build_audio(lines, wd, sr=24000):
    """Jede Zeile einzeln sprechen, mit kurzer Pause aneinanderhängen. Liefert Zeiten je Zeile und Wort."""
    parts, t = [], 0.35
    parts.append(np.zeros(int(sr * t), np.int16))
    for i, ln in enumerate(lines):
        c = CAST[ln["wer"]]
        mp3 = wd / f"l{i:02d}.mp3"
        words = tts_line(ln["say"], c["voice"], c["rate"], c["pitch"], mp3)
        a = pcm(mp3, sr)
        ln["t0"], ln["t1"] = t, t + len(a) / sr
        ln["words"] = [(t + s, t + e, w) for s, e, w in words]
        parts.append(a)
        gap = 0.32 if i + 1 < len(lines) and lines[i + 1]["wer"] != ln["wer"] else 0.18
        parts.append(np.zeros(int(sr * gap), np.int16))
        t = ln["t1"] + gap
    audio = np.concatenate(parts)
    import wave
    with wave.open(str(wd / "voice.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(audio.tobytes())
    return t + 2.4  # Platz für die Schlusskarte


def text_chunks(text, t0, t1, maxc=30):
    """Untertitel aus dem Anzeigetext (mit Ziffern und Satzzeichen), zeitlich nach Zeichenanteil verteilt."""
    toks = text.split()
    out, cur = [], []
    for w in toks:
        if cur and len(" ".join(cur + [w])) > maxc:
            out.append(cur)
            cur = []
        cur.append(w)
        if w[-1:] in ".?!:" and len(cur) > 1:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    total = sum(len(" ".join(c)) for c in out) or 1
    res, t = [], t0
    for c in out:
        dur = (t1 - t0) * len(" ".join(c)) / total
        res.append((t, t + dur, " ".join(c)))
        t += dur
    return res


def chunks(words, maxc=30):
    out, cur = [], []
    for w in words:
        if cur and len(" ".join(x[2] for x in cur + [w])) > maxc:
            out.append(cur)
            cur = []
        cur.append(w)
        if w[2][-1:] in ".?!:" and len(cur) > 1:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return [(c[0][0], c[-1][1], " ".join(x[2] for x in c)) for c in out]


# ---------------------------------------------------------------- Bilder
_shot_cache = {}


def shot(key):
    if not (ASSETS / f"{key}.jpg").exists():
        key = next((k for k in ("wohnzimmer_alle", "kueche_alle", *SHOTS) if (ASSETS / f"{k}.jpg").exists()))
    if key not in _shot_cache:
        im = Image.open(ASSETS / f"{key}.jpg").convert("RGB")
        s = max(W / im.width, H / im.height) * 1.08          # Reserve für Kamerafahrt
        _shot_cache[key] = im.resize((int(im.width * s), int(im.height * s)), Image.LANCZOS)
    return _shot_cache[key]


def camera(im, u, seed):
    """Langsame Fahrt: Zoom 1.00→1.07 (oder zurück) plus leichter Schwenk, u = 0..1 über die Einstellung."""
    rnd = random.Random(seed)
    zin = rnd.random() < 0.65
    z = 1.0 + 0.07 * (u if zin else 1 - u)
    dx, dy = rnd.uniform(-0.5, 0.5), rnd.uniform(-0.3, 0.3)
    cw, ch = im.width / 1.08 / z, im.height / 1.08 / z
    cx = im.width / 2 + dx * (im.width - cw) / 2 * (u - 0.5) * 2
    cy = im.height / 2 + dy * (im.height - ch) / 2 * (u - 0.5) * 2
    box = (cx - cw / 2, cy - ch / 2, cx + cw / 2, cy + ch / 2)
    return im.resize((W, H), Image.BILINEAR, box=box)


_vign = None


def vignette():
    """Oben und unten leicht abdunkeln, damit Karten und Untertitel lesbar bleiben."""
    global _vign
    if _vign is None:
        y = np.linspace(0, 1, H)
        a = np.clip(0.42 - y * 1.4, 0, 1) * 0.9 + np.clip((y - 0.72) * 1.6, 0, 1) * 0.55
        arr = np.zeros((H, W, 4), np.uint8)
        arr[..., 3] = (a[:, None] * 255).astype(np.uint8)
        _vign = Image.fromarray(arr, "RGBA")
    return _vign


# ---------------------------------------------------------------- Karten (oben)
CARD_W = 900
CX = (W - CARD_W) // 2


def shadow_box(lay, box, r):
    from PIL import ImageFilter
    sh = Image.new("RGBA", lay.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle((box[0], box[1] + 10, box[2], box[3] + 14), radius=r, fill=(0, 0, 0, 120))
    lay.alpha_composite(sh.filter(ImageFilter.GaussianBlur(16)))


def card_titel(titel, unter):
    """Hook-Titel wie eine Schlagzeile: dunkle Box, weiße Versalien, farbige Unterzeile."""
    lay = Image.new("RGBA", (W, 420), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    f1, f2 = font(76, "ExtraBold"), font(38, "ExtraBold")
    l1 = wrap(d, titel.upper(), f1, 860)[:2]
    l2 = wrap(d, unter.upper(), f2, 860)[:2]
    w = max([tw(d, x, f1) for x in l1] + [tw(d, x, f2) for x in l2]) + 80
    h = len(l1) * 88 + len(l2) * 48 + 56
    x0 = (W - w) / 2
    shadow_box(lay, (x0, 20, x0 + w, 20 + h), 22)
    rr(d, (x0, 20, x0 + w, 20 + h), 22, (22, 24, 23, 240))
    y = 44
    for ln in l1:
        d.text((W / 2, y), ln, font=f1, fill=WHITE, anchor="mt")
        y += 88
    for ln in l2:
        d.text((W / 2, y + 4), ln, font=f2, fill=ORANGE, anchor="mt")
        y += 48
    return lay.crop((0, 0, W, int(20 + h + 30)))


def card_zahlen(k):
    """Zwei Zahlen nebeneinander (z. B. 599 € Pflegegeld | bis 1.497 € Pflegedienst)."""
    lay = Image.new("RGBA", (W, 300), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    y = 10
    if k.get("tag"):
        ft = font(30, "ExtraBold")
        tw_ = tw(d, k["tag"].upper(), ft) + 30
        rr(d, (CX, y, CX + tw_, y + 46), 8, (22, 24, 23, 235))
        d.text((CX + 15, y + 8), k["tag"].upper(), font=ft, fill=WHITE)
        y += 58
    boxes = [b for b in (k.get("a"), k.get("b")) if b]
    bw = (CARD_W - 20 * (len(boxes) - 1)) / max(1, len(boxes))
    for i, b in enumerate(boxes):
        x = CX + i * (bw + 20)
        shadow_box(lay, (x, y, x + bw, y + 170), 18)
        rr(d, (x, y, x + bw, y + 170), 18, WHITE)
        col = ORANGE if i == 0 else GREEN
        fv = font(78 if len(b.get("wert", "")) < 9 else 62, "ExtraBold")
        d.text((x + bw / 2, y + 22), b.get("wert", ""), font=fv, fill=col, anchor="mt")
        d.text((x + bw / 2, y + 120), b.get("text", ""), font=font(32, "Bold"), fill=INK, anchor="mt")
    return lay.crop((0, 0, W, int(y + 200)))


def card_bon(k, mini=False):
    """Kassenbon: Posten, Summe, Kleingedrucktes, Zackenrand."""
    s = 0.62 if mini else 1.0
    bw = int(640 * s)
    lay = Image.new("RGBA", (W, int(560 * s) + 60), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    x0 = (W - bw) // 2 if not mini else CX
    posten = k.get("posten") or []
    fh, fp, fs, fk = mono(int(36 * s)), mono(int(30 * s)), mono(int(56 * s)), font(int(22 * s), "Medium")
    h = int((110 + 52 * len(posten) + 110 + (70 if k.get("fuss") else 0)) * s)
    shadow_box(lay, (x0, 10, x0 + bw, 10 + h), 6)
    rr(d, (x0, 10, x0 + bw, 10 + h), 6, (252, 251, 247))
    for zx in range(x0, x0 + bw, int(20 * s) or 1):   # Zackenrand unten
        d.polygon([(zx, 10 + h), (zx + 10 * s, 10 + h + 12 * s), (zx + 20 * s, 10 + h)], fill=(252, 251, 247))
    y = 10 + int(34 * s)
    d.text((x0 + bw / 2, y), (k.get("titel") or "").upper(), font=fh, fill=INK, anchor="mt")
    y += int(70 * s)
    for name, preis in posten:
        d.text((x0 + 30 * s, y), name, font=fp, fill=INK)
        d.text((x0 + bw - 30 * s, y), preis, font=fp, fill=INK, anchor="ra")
        y += int(52 * s)
    d.line((x0 + 30 * s, y + 8 * s, x0 + bw - 30 * s, y + 8 * s), fill=(150, 150, 150), width=2)
    y += int(28 * s)
    d.text((x0 + bw - 30 * s, y), k.get("summe", ""), font=fs, fill=INK, anchor="ra")
    y += int(80 * s)
    if k.get("fuss"):
        for i, ln in enumerate(wrap(d, k["fuss"], fk, bw - 60 * s)[:3]):
            d.text((x0 + 30 * s, y + i * 26 * s), ln, font=fk, fill=MUTED)
    return lay.crop((0, 0, W, int(10 + h + 20 * s)))


def card_baustein(k, tags):
    """Kosten-Baustein: Kopfzeile, gesammelte Schlagworte, aktuelle Karte mit Etikett."""
    lay = Image.new("RGBA", (W, 420), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    y = 6
    if k.get("kopf"):
        fk = font(30, "ExtraBold")
        w = tw(d, k["kopf"].upper(), fk) + 28
        rr(d, (CX, y, CX + w, y + 44), 6, (22, 24, 23, 235))
        d.text((CX + 14, y + 7), k["kopf"].upper(), font=fk, fill=WHITE)
        y += 54
    x = CX
    ft = font(24, "Bold")
    for t in tags:
        w = tw(d, t, ft) + 20
        rr(d, (x, y, x + w, y + 36), 6, (232, 240, 236))
        d.text((x + 10, y + 6), t, font=ft, fill=GREEN_D)
        x += w + 8
    if tags:
        y += 46
    fb, fs = font(44, "ExtraBold"), font(24, "Medium")
    lines = wrap(d, k.get("titel", ""), fb, CARD_W - 60)[:2]
    small = wrap(d, k.get("fuss", ""), fs, CARD_W - 60)[:2] if k.get("fuss") else []
    h = 70 + len(lines) * 54 + len(small) * 30 + 20
    shadow_box(lay, (CX, y, CX + CARD_W, y + h), 18)
    rr(d, (CX, y, CX + CARD_W, y + h), 18, WHITE)
    fe = font(24, "ExtraBold")
    et = (k.get("etikett") or "").upper()
    if et:
        w = tw(d, et, fe) + 22
        rr(d, (CX + 26, y + 22, CX + 26 + w, y + 56), 6, GREEN)
        d.text((CX + 37, y + 27), et, font=fe, fill=WHITE)
    yy = y + 70
    for ln in lines:
        d.text((CX + 28, yy), ln, font=fb, fill=INK)
        yy += 54
    for ln in small:
        d.text((CX + 28, yy + 4), ln, font=fs, fill=MUTED)
        yy += 30
    return lay.crop((0, 0, W, int(y + h + 30)))


def card_hinweis(k):
    lay = Image.new("RGBA", (W, 120), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    f = font(40, "ExtraBold")
    t = k.get("text", "")
    w = min(CARD_W, tw(d, t, f) + 50)
    x0 = (W - w) / 2
    shadow_box(lay, (x0, 10, x0 + w, 80), 14)
    rr(d, (x0, 10, x0 + w, 80), 14, (255, 190, 70))
    d.text((W / 2, 45), t, font=f, fill=INK, anchor="mm")
    return lay


def card_balken(k):
    """Vergleich als Balken (wächst in der Animation)."""
    zeilen = k.get("zeilen") or []
    lay = Image.new("RGBA", (W, 110 + 130 * len(zeilen)), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    h = 50 + 130 * len(zeilen)
    shadow_box(lay, (CX, 10, CX + CARD_W, 10 + h), 18)
    rr(d, (CX, 10, CX + CARD_W, 10 + h), 18, WHITE)
    return lay, zeilen


def card_liste(k):
    punkte = [str(p) for p in (k.get("punkte") or [])][:6]
    tmp = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    fp = font(34, "Bold")
    rows = [wrap(tmp, p, fp, CARD_W - 130)[:2] for p in punkte]
    h = 100 + sum(14 + 44 * len(r) for r in rows) + 10
    lay = Image.new("RGBA", (W, h + 60), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    shadow_box(lay, (CX, 10, CX + CARD_W, 10 + h), 18)
    rr(d, (CX, 10, CX + CARD_W, 10 + h), 18, WHITE)
    d.text((CX + 30, 34), k.get("titel", ""), font=font(42, "ExtraBold"), fill=INK)
    y = 100
    for r in rows:
        d.ellipse((CX + 30, y + 4, CX + 68, y + 42), fill=GREEN)
        d.text((CX + 49, y + 23), "✓", font=font(26, "ExtraBold"), fill=WHITE, anchor="mm")
        for j, ln in enumerate(r):
            d.text((CX + 90, y + 2 + j * 44), ln, font=fp, fill=INK)
        y += 14 + 44 * len(r)
    return lay.crop((0, 0, W, 10 + h + 30))


def build_cards(lines, titel, unter):
    """Für jede Zeile die sichtbaren Karten festlegen (Karten bleiben, bis eine neue kommt)."""
    stack, tags, anchor, cur = [], [], None, None
    for i, ln in enumerate(lines):
        k = ln.get("karte")
        if isinstance(k, dict) and k.get("typ") and k["typ"] != "keins":
            typ = k["typ"]
            if typ == "weg":
                cur, anchor = None, None
            else:
                if cur is not None and cur[0] == "bon" and typ != "bon":
                    anchor = ("bon_mini", cur[1])              # Bon bleibt klein oben stehen
                if typ == "baustein":
                    if cur is not None and cur[0] == "baustein" and cur[1].get("etikett"):
                        tags.append(cur[1]["etikett"].title() if len(cur[1]["etikett"]) > 3 else cur[1]["etikett"])
                else:
                    tags = []
                cur = (typ, k, list(tags))
        stack.append((anchor, cur, i < 3 and not any(isinstance(lines[j].get("karte"), dict) and lines[j]["karte"].get("typ") in ("bon", "baustein", "balken", "liste") for j in range(0, i + 1))))
    # Ebenen vorbereiten (gleiche Karte = gleiche Ebene, damit sie nicht neu einfliegt)
    cache = {}

    def layer(spec):
        if spec is None:
            return None
        key = json.dumps(spec, ensure_ascii=False, sort_keys=True, default=str)
        if key not in cache:
            typ, k = spec[0], spec[1]
            if typ == "titel":
                im = card_titel(titel, unter)
            elif typ == "zahlen":
                im = card_zahlen(k)
            elif typ == "bon":
                im = card_bon(k)
            elif typ == "bon_mini":
                im = card_bon(k, mini=True)
            elif typ == "baustein":
                im = card_baustein(k, spec[2])
            elif typ == "hinweis":
                im = card_hinweis(k)
            elif typ == "balken":
                im = card_balken(k)
            elif typ == "liste":
                im = card_liste(k)
            else:
                im = None
            cache[key] = (key, im, spec)
        return cache[key]

    out = []
    for anchor, cur, show_title in stack:
        layers = []
        if show_title:
            layers.append(layer(("titel", {})))
        if anchor:
            layers.append(layer(anchor))
        if cur:
            layers.append(layer(cur))
        out.append([x for x in layers if x and x[1] is not None])
    return out


def draw_balken(fr, im, zeilen, y0, prog):
    fr.alpha_composite(im, (0, int(y0)))
    d = ImageDraw.Draw(fr)
    mx = max([float(z.get("wert", 0) or 0) for z in zeilen] + [1])
    for i, z in enumerate(zeilen):
        y = y0 + 40 + i * 130
        d.text((CX + 30, y), z.get("label", ""), font=font(36, "ExtraBold"), fill=INK)
        d.rounded_rectangle((CX + 30, y + 54, CX + CARD_W - 30, y + 84), radius=15, fill=(236, 236, 232))
        k = ease_out(max(0.0, min(1.0, prog * 1.6 - i * 0.35)))
        w = (CARD_W - 60) * (float(z.get("wert", 0) or 0) / mx) * k
        if w > 4:
            d.rounded_rectangle((CX + 30, y + 54, CX + 30 + w, y + 84), radius=15, fill=ORANGE if i else GREEN)


# ---------------------------------------------------------------- Untertitel
def subtitle(name, col, text):
    f, fn = font(50, "ExtraBold"), font(24, "ExtraBold")
    tmp = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    lines = wrap(tmp, text, f, 900)[:2]
    w = max(tw(tmp, x, f) for x in lines) + 44
    h = len(lines) * 64 + 20
    lay = Image.new("RGBA", (W, h + 60), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    x0 = 60
    nw = tw(d, name, fn) + 22
    rr(d, (x0, 0, x0 + nw, 38), 6, col + (255,))
    d.text((x0 + 11, 6), name, font=fn, fill=INK)
    rr(d, (x0, 46, x0 + w, 46 + h), 10, (18, 20, 19, 215))
    for i, ln in enumerate(lines):
        d.text((x0 + 22, 56 + i * 64), ln, font=f, fill=WHITE)
    return lay


SUB_Y = 1240


# ---------------------------------------------------------------- Rendern
def cover(data):
    fr = camera(shot("wohnzimmer_alle"), 0.5, 1).convert("RGBA")
    fr.alpha_composite(vignette())
    t = card_titel(data["titel"], data.get("unter", ""))
    fr.alpha_composite(t, (0, 260))
    d = ImageDraw.Draw(fr)
    f = font(40, "ExtraBold")
    w = tw(d, "PFLEGE MIT PLAN", f) + 50
    rr(d, ((W - w) / 2, 1560, (W + w) / 2, 1630), 35, GREEN)
    d.text((W / 2, 1595), "PFLEGE MIT PLAN", font=f, fill=WHITE, anchor="mm")
    return fr


def end_card(fr, k):
    d = ImageDraw.Draw(fr)
    a = int(200 * k)
    ov = Image.new("RGBA", (W, H), (14, 34, 27, a))
    fr.alpha_composite(ov)
    if k > 0.3:
        f1, f2 = font(70, "ExtraBold"), font(44, "Bold")
        d.text((W / 2, 820), "Mehr Hilfe für Angehörige:", font=f2, fill=WHITE, anchor="mm")
        d.text((W / 2, 920), "pflege-mit-plan.com", font=f1, fill=(215, 240, 228), anchor="mm")


def render(data, wd):
    wd.mkdir(parents=True, exist_ok=True)
    lines = [ln for ln in data["zeilen"] if ln.get("say") and ln.get("wer") in CAST]
    total = build_audio(lines, wd)
    cards = build_cards(lines, data["titel"], data.get("unter", ""))
    for i, ln in enumerate(lines):
        c = CAST[ln["wer"]]
        sk = ln.get("shot") if ln.get("shot") in SHOTS else c["shot"]
        ln["_shot"] = sk
        shown = (ln.get("text") or "").strip() or ln["say"]
        ln["_subs"] = [(a, z, subtitle(c["name"], c["col"], tx)) for a, z, tx in text_chunks(shown, ln["t0"], ln["t1"])]
    # Einstellungen: gleiche Einstellung hintereinander = eine durchgehende Kamerafahrt
    segs = []
    for i, ln in enumerate(lines):
        t0 = 0.0 if i == 0 else ln["t0"] - 0.12
        if segs and segs[-1]["shot"] == ln["_shot"]:
            segs[-1]["t1"] = ln["t1"]
        else:
            if segs:
                segs[-1]["t1"] = t0
            segs.append({"shot": ln["_shot"], "t0": t0, "t1": ln["t1"], "seed": i})
    segs[-1]["t1"] = total
    cov = cover(data)
    cov.convert("RGB").save(wd / "cover.jpg", quality=92)
    music = wd / "music.wav"
    try:
        from video.ambient_music import build_music_loop
        build_music_loop(music, seed=data.get("thema", "pflege"), minutes=max(1.5, total / 60 + 0.2))
    except Exception as e:  # noqa: BLE001
        print("  Musik übersprungen:", e)
        music = None
    vid = wd / "video_silent.mp4"
    ff = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                           "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", str(vid)],
                          stdin=subprocess.PIPE)
    vg = vignette()
    cov_b = cov.convert("RGB").tobytes()
    n = int(total * FPS)
    si, li = 0, 0
    first_seen = {}
    for fi in range(n):
        if fi < 3:                     # Titelbild als erste Bilder (TikTok-Vorschau)
            ff.stdin.write(cov_b)
            continue
        t = fi / FPS
        while si + 1 < len(segs) and t >= segs[si + 1]["t0"]:
            si += 1
        while li + 1 < len(lines) and t >= lines[li + 1]["t0"] - 0.12:
            li += 1
        sg = segs[si]
        u = (t - sg["t0"]) / max(0.5, sg["t1"] - sg["t0"])
        fr = camera(shot(sg["shot"]), max(0.0, min(1.0, u)), sg["seed"]).convert("RGBA")
        if t - sg["t0"] < 0.12 and si > 0:          # weicher Schnitt: kurzer Weißblitz-Fade
            k = 1 - (t - sg["t0"]) / 0.12
            fr.alpha_composite(Image.new("RGBA", (W, H), (255, 255, 255, int(60 * k))))
        fr.alpha_composite(vg)
        # Karten
        y = 150
        for key, im, spec in cards[li]:
            if key not in first_seen:
                first_seen[key] = t
            age = t - first_seen[key]
            k = ease_back(min(1.0, age / 0.4))
            if spec[0] == "balken":
                lay, zeilen = im
                if k < 1:
                    pass
                draw_balken(fr, lay, zeilen, y + 30 * (1 - k), age / 1.2)
                y += lay.height + 10
                continue
            if k < 1:
                s = 0.92 + 0.08 * k
                ii = im.resize((int(im.width * s), int(im.height * s)), Image.BILINEAR)
                ii.putalpha(ii.split()[3].point(lambda v, kk=min(1.0, age / 0.25): int(v * kk)))
                fr.alpha_composite(ii, (int((W - ii.width) / 2), int(y + 20 * (1 - k))))
            else:
                fr.alpha_composite(im, (0, int(y)))
            y += im.height + 6
        # Fortschritt + Marke
        d = ImageDraw.Draw(fr)
        d.rectangle((0, 0, int(W * min(1.0, t / total)), 7), fill=(215, 240, 228))
        # Untertitel
        ln = lines[li]
        for a, z, sub in ln["_subs"]:
            if a - 0.05 <= t < z + 0.2:
                kk = ease_back(min(1.0, (t - a + 0.05) / 0.16))
                if kk < 1:
                    s = 0.94 + 0.06 * kk
                    ss = sub.resize((int(sub.width * s), int(sub.height * s)), Image.BILINEAR)
                    fr.alpha_composite(ss, (int(60 * (1 - s)), SUB_Y + int(10 * (1 - kk))))
                else:
                    fr.alpha_composite(sub, (0, SUB_Y))
                break
        if t > total - 2.2:
            end_card(fr, ease_out(min(1.0, (t - (total - 2.2)) / 0.5)))
        ff.stdin.write(fr.convert("RGB").tobytes())
    ff.stdin.close()
    ff.wait()
    out = wd / "video.mp4"
    inputs = ["-i", str(vid), "-i", str(wd / "voice.wav")]
    if music and music.exists():
        inputs += ["-stream_loop", "-1", "-i", str(music)]
        af = ("[1:a]aformat=sample_rates=44100:channel_layouts=stereo,volume=1.0[v];"
              "[2:a]aformat=sample_rates=44100:channel_layouts=stereo,volume=0.08[m];[v][m]amix=inputs=2:duration=first:normalize=0[a]")
    else:
        af = "[1:a]aformat=sample_rates=44100:channel_layouts=stereo[a]"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", af, "-map", "0:v", "-map", "[a]",
                    "-t", f"{total:.2f}", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)], check=True)
    return out, total


# ---------------------------------------------------------------- Skript (Claude)
FIGUREN = """Feste Figuren (immer gleich, wie eine kleine Serie):
- hilde: Hilde, 84, Mutter, hat Pflegegrad 3, trocken-humorvoll, eigensinnig, herzlich.
- anja: Anja, 52, ihre Tochter, pflegt Hilde neben dem Job, stellt die Fragen der Zuschauer, manchmal überfordert.
- jana: Jana, 30, Pflegefachkraft beim ambulanten Pflegedienst und Praxisanleiterin, erklärt klar und freundlich.
- becker: Herr Becker, Pflegeberater (Pflegestützpunkt), erklärt Anträge, Geld und neue Regeln.
- leon: Leon, 19, Pflege-Azubi im 1. Jahr, neugierig, macht typische Anfängerfehler, fragt nach."""

# Robin 25.11.2026: höchstens 1 News pro Tag, sonst Info-Videos für Angehörige und Lernvideos für Azubis – alles in diesem Design
REIHEN = {
    "angehoerige": {
        "ziel": "Info-Video für pflegende Angehörige (Anrede „Sie“, wenn jemand die Zuschauer anspricht).",
        "figuren": "hilde, anja, jana, becker (Becker nur bei Anträgen/Beratung)",
        "fakten": "Fakten und Beträge NUR aus dem mitgegebenen Seitentext von pflege-mit-plan.com. Steht etwas nicht darin, lass es weg.",
        "schluss": "Letzte Zeile: Jana oder Herr Becker mit dem wichtigsten Tipp + Frage an die Zuschauer.",
    },
    "azubi": {
        "ziel": "Lernvideo für Pflege-Azubis (Pflegefachfrau/-mann und Pflegeassistenz), prüfungsnah, Anrede „du“.",
        "figuren": "jana (Praxisanleiterin) und leon (Azubi); höchstens eine kurze Zeile von hilde als Pflegebeispiel",
        "fakten": ("Nur gesichertes Lehrbuch-Grundwissen. KEINE Medikamente, Wirkstoffe, Dosierungen oder ärztlichen Entscheidungen. "
                   "Normalwerte nur als allgemein anerkannte Erwachsenen-Richtwerte mit dem Hinweis, dass Einrichtungsstandard und Praxisanleitung gelten. "
                   "Wo sich Aufgaben unterscheiden, kurz sagen, was die Assistenz übernimmt und was der Fachkraft vorbehalten ist. "
                   "Keine erfundenen Studien oder Zahlen."),
        "schluss": "Letzte Zeile: Jana mit Merksatz für die Prüfung + Frage an die Zuschauer.",
    },
    "news": {
        "ziel": "Aktuelle Pflege-Nachricht als Alltagsszene: Anja (oder Leon) hat davon gehört oder gelesen, Herr Becker oder Jana ordnet ein.",
        "figuren": "anja, becker, jana, hilde (Hilde für eine humorvolle Zeile); bei Nachrichten für Pflegekräfte: jana und leon",
        "fakten": ("Fakten NUR aus dem mitgegebenen Faktenblatt (belegt, mit Quelle). Nichts erfinden. "
                   "Eine Karte {\"typ\":\"hinweis\",\"text\":\"Quelle: <Name>\"} zeigt die Quelle."),
        "schluss": "Letzte Zeile: Was das konkret bedeutet + Frage an die Zuschauer.",
    },
}

SYSTEM = ("Du schreibst kurze Dialog-Szenen für TikTok/YouTube-Shorts des Kanals „Pflege mit Plan“ (Hilfe rund um Pflege in Deutschland).\n"
          + FIGUREN + "\n"
          "Stil: echter Alltagsdialog, kurze gesprochene Sätze, Umgangssprache, am Anfang ein kleiner Konflikt, eine Frage oder ein Missverständnis, "
          "dann Aufklärung mit konkreten Fakten, eine humorvolle Zeile, am Ende ein klarer Tipp.\n"
          "Keine echten Personen, keine Marken von Pflegediensten, keine Patientendaten, keine Rechtsberatung („das prüft die Pflegekasse“ ist ok), "
          "keine Heilversprechen.\nAntworte ausschließlich mit einem JSON-Objekt (ohne Markdown).")

FORMAT = """JSON-Format:
{
 "thema": "kurzer Titel fürs Video (max. 70 Zeichen)",
 "titel": "Hook oben im Bild, max. 4 Wörter, z. B. „Tochter pflegt?“",
 "unter": "Unterzeile zum Hook, max. 6 Wörter, z. B. „Dann zahlt die Kasse weniger.“",
 "caption": "TikTok-Text: 1-2 Sätze mit Hauptsuchwort, 1 Emoji, eine Frage. Ohne Hashtags.",
 "hashtags": ["#pflege", "... 4 weitere passende"],
 "zeilen": [ {"wer": "anja", "say": "Gesprochener Satz.", "text": "Untertitel", "shot": "anja_nah", "karte": null} ]
}
Regeln für "zeilen" (18-26 Zeilen, zusammen 120-160 Wörter – nicht mehr! –, ca. 60-90 Sekunden):
- "wer": eine der erlaubten Figuren.
- "say": 3-14 Wörter, so wie man es spricht; Zahlen ausgeschrieben, wie gesprochen („fünfhundertneunundneunzig Euro“).
- "text": derselbe Satz als Untertitel, mit Ziffern und Satzzeichen („599 Euro.“).
- "shot": passende Einstellung aus dieser Liste (zur sprechenden Person oder zur Handlung): SHOTS
  Möglichst oft die Einstellung der sprechenden Person; Totale zum Einstieg und für Pointen.
- "karte" (oben im Bild, bleibt stehen, bis eine neue kommt; null = nichts ändern). Jede genannte Zahl wird als Karte gezeigt. Typen:
  {"typ":"zahlen","tag":"PFLEGEGRAD 3","a":{"wert":"599 €","text":"Pflegegeld"},"b":{"wert":"bis 1.497 €","text":"Pflegedienst"}}
  {"typ":"bon","titel":"1 Hausbesuch","posten":[["Große Grundpflege","31,79 €"],["Hausbesuchspauschale","5,79 €"]],"summe":"37,58 €","fuss":"Beispielrechnung"}
  {"typ":"baustein","kopf":"Davon zahlt der Dienst","etikett":"Lohn","titel":"Tarifgehalt der Pflegekraft","fuss":"kurzer Beleg"}  (mehrere hintereinander mit gleichem "kopf" sammeln die Etiketten oben)
  {"typ":"hinweis","text":"kurzer Merksatz"}
  {"typ":"liste","titel":"So geht's:","punkte":["Schritt eins","Schritt zwei"]}  (wächst: in Folgezeilen dieselbe Liste mit einem Punkt mehr)
  {"typ":"balken","zeilen":[{"label":"Pflegesachleistung","wert":1497},{"label":"Pflegegeld","wert":599}]}
  {"typ":"weg"} (Karten ausblenden)
  Insgesamt 5-10 Karten-Wechsel, mindestens 2 verschiedene Typen. Ein Bon nur bei echten Beispielrechnungen.
- Die ersten 2 Zeilen: der Konflikt bzw. die Frage. Keine Begrüßung."""


def _ask(client, user):
    last = ""
    for _ in range(2):
        resp = client.messages.create(model=MODEL, max_tokens=8000, system=SYSTEM, messages=[{"role": "user", "content": user}])
        last = "".join(b.text for b in resp.content if b.type == "text")
        i, j = last.find("{"), last.rfind("}")
        try:
            data = json.loads(last[i:j + 1])
            n = sum(len(str(z.get("say", "")).split()) for z in data.get("zeilen") or [])
            if len(data.get("zeilen") or []) >= 12 and (n <= 185 or _ == 1):
                return data
            if n > 185:
                print(f"  Skript zu lang ({n} Wörter), kürzer")
                user += f" WICHTIG: Dein letzter Entwurf hatte {n} Wörter. Höchstens 160 Wörter!"
        except Exception as e:  # noqa: BLE001
            print("  JSON-Fehler, neuer Versuch:", e)
    raise RuntimeError(f"kein gültiges Skript: {last[-300:]}")


def news_facts(client, avoid):
    """Recherche mit Websuche (wie bisher bei den Pflege-News) -> Faktenblatt mit Quelle."""
    from pflege.tiktok_news import NEWS_MODEL, SYSTEM as NEWS_SYSTEM
    today = dt.datetime.now(TZ).strftime("%d.%m.%Y")
    research = (f"Heute ist der {today}. Suche EINE aktuelle, belegte Pflege-Nachricht aus Deutschland (letzte 30 Tage), die pflegende Angehörige "
                "oder Pflegekräfte direkt betrifft: neue Regeln, Pflegegeld/Leistungen, Gerichtsurteile, Abrechnungsbetrug, Personalmangel, Studien. "
                f"Diese Themen gab es schon (nicht wiederholen): {avoid or 'keine'}.\n"
                "Gib ein kurzes Faktenblatt aus. Erste Zeile genau so: QUELLE: <Name> | <URL>. Dann Thema, Zielgruppe (Angehörige oder Pflegekräfte) "
                "und 6-12 belegte Fakten mit Zahlen. Noch kein Skript.")
    msgs, parts = [{"role": "user", "content": research}], []
    for _ in range(5):
        resp = client.messages.create(model=NEWS_MODEL, max_tokens=8000, system=NEWS_SYSTEM.rsplit("\n", 1)[0],
                                      tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}], messages=msgs)
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
    m = re.search(r"QUELLE:\s*(.+?)\s*\|\s*(https?://\S+)", facts)
    return facts, (m.group(1).strip() if m else ""), (m.group(2).strip().rstrip(").,") if m else "")


def script(reihe, topic, avoid):
    import anthropic
    client = anthropic.Anthropic()
    r = REIHEN[reihe]
    shots = ", ".join(f"{k} ({v})" for k, v in SHOTS.items())
    head = f"Reihe: {r['ziel']}\nErlaubte Figuren: {r['figuren']}\nFAKTEN-REGEL: {r['fakten']}\n{r['schluss']}\n"
    quelle, url = "pflege-mit-plan.com", SITE
    if reihe == "angehoerige":
        from pflege.make import page_text
        url = SITE + topic[2]
        text = page_text(url)
        if len(text) < 400:
            raise RuntimeError(f"Seite zu kurz: {url}")
        body = f"Thema: {topic[1]}\nSeite: {url}\nSeitentext (einzige Faktenquelle):\n{text}\n"
    elif reihe == "azubi":
        body = f"Thema: {topic[1]}\n"
    else:
        facts, quelle, url = news_facts(client, avoid)
        body = f"Faktenblatt (einzige Faktenquelle):\n{facts}\n"
        avoid = None
    user = (f"{head}\n{body}\n" + (f"Schon gemacht (anderen Blickwinkel wählen): {avoid}\n" if avoid else "")
            + "\n" + FORMAT.replace("SHOTS", shots) + "\n\nAntworte nur mit dem JSON-Objekt.")
    data = _ask(client, user)
    data["quelle"], data["quelle_url"] = quelle or "pflege-mit-plan.com", url or SITE
    return data


def pick_topic(reihe, st):
    pool = ANGEHOERIGE if reihe == "angehoerige" else AZUBI
    used = st.setdefault("used", {})
    least = min(used.get(t[0], 0) for t in pool)
    return random.choice([t for t in pool if used.get(t[0], 0) == least])


PLAN = ["news", "angehoerige", "azubi"]
YT_SLOTS = ["09:30", "12:30", "18:30"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", default=",".join(PLAN), help="Reihen in dieser Reihenfolge, z. B. news,angehoerige,azubi")
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--script", default="")
    ap.add_argument("--topic", default="")
    a = ap.parse_args()
    import projekt
    tt_on, yt_on = projekt.aktiv("tt-pflege"), projekt.aktiv("yt-pflege")
    if not a.test and not a.script and not tt_on and not yt_on:
        print("Kanal-Schalter: Pflege mit Plan (TikTok + YouTube) ruht – nichts zu tun.")
        return
    st = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    st.setdefault("used", {})
    st.setdefault("themen", [])
    day = dt.datetime.now(TZ).date().isoformat()
    plan = [p for p in a.plan.split(",") if p in REIHEN]
    if a.script:
        plan = plan[:1] or ["angehoerige"]
    ok = 0
    for i, reihe in enumerate(plan):
        try:
            topic = None
            if a.script:
                data = json.loads(Path(a.script).read_text(encoding="utf-8"))
            else:
                if reihe != "news":
                    pool = ANGEHOERIGE if reihe == "angehoerige" else AZUBI
                    topic = next((t for t in pool if t[0] == a.topic), None) or pick_topic(reihe, st)
                    avoid = [x["thema"] for x in st["themen"][-60:] if x.get("slug") == topic[0]]
                else:
                    avoid = [x["thema"] for x in st["themen"][-60:] if x.get("reihe") == "news"]
                data = script(reihe, topic, avoid)
            print(f"[{i}] {reihe}: {data['thema']} ({data.get('quelle')})")
            wd = OUT / day / f"{i}-{reihe}"
            wd.mkdir(parents=True, exist_ok=True)
            (wd / "script.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            path, secs = render(data, wd)
            print(f"  gerendert: {path} ({secs:.0f} s)")
            if not a.test and not a.script:
                if tt_on:
                    queue(path, data, secs)
                if yt_on:
                    try:
                        print("  YouTube-Short:", youtube_short(path, data, YT_SLOTS[ok % len(YT_SLOTS)]))
                    except Exception as e:  # noqa: BLE001
                        print("  YouTube FEHLER:", str(e)[:300])
                if topic:
                    st["used"][topic[0]] = st["used"].get(topic[0], 0) + 1
                st["themen"].append({"thema": data["thema"], "reihe": reihe, "slug": topic[0] if topic else None,
                                     "url": data.get("quelle_url"), "datum": day})
                st["themen"] = st["themen"][-300:]
                STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
            ok += 1
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            print("  FEHLER:", str(e)[:400])
    if ok == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
