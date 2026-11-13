"""Altovate Reels – automatische Produktion für das Kundenportal (reels-portal).

Ablauf pro Lauf (GitHub-Workflow reels-worker, alle 15 Min):
  1. Offene Aufträge holen (neue Briefings + Änderungswünsche)
  2. Claude schreibt pro Reel Hook, Szenen, CTA und Farbwelt aus dem Briefing
  3. Pillow rendert 1080x1920-Frames (Bewegungstext, Kundenbilder mit Zoom, Übergänge), ffmpeg + synthetische Musik
  4. Finale Datei (ohne Wasserzeichen) + Vorschau (720p, Wasserzeichen) hochladen → Status 'pruefung'
  5. Sind alle Videos fertig, legt die Edge-Funktion eine Freigabe für Robin an (Telegram ✅/❌).

DATENSCHUTZ: Das Repo ist öffentlich, die Action-Logs auch. Niemals Kundendaten (Namen, Texte, Briefing,
URLs) ausgeben – nur Auftragsnummern und Fehlertypen.
"""
import io
import json
import math
import os
import random
import subprocess
import sys
import tempfile
import traceback
import wave
from pathlib import Path

import anthropic
import numpy as np
import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import config  # noqa: E402

API = "https://kfiiaqjzzwwacthzrcut.supabase.co/functions/v1/reels-portal"
TOKEN = os.environ.get("REELS_WORKER_TOKEN", "").strip()
W, H, FPS = 1080, 1920, 30
SR = 44100


def font_path(*names):
    dirs = ["/usr/share/fonts/truetype/montserrat", "/usr/share/fonts/opentype/montserrat", "/usr/share/fonts/truetype/liberation",
            r"C:\Windows\Fonts"]
    for n in names:
        for d in dirs:
            p = Path(d) / n
            if p.exists():
                return str(p)
    return None


FONT_BLACK = font_path("Montserrat-Black.ttf", "Montserrat-ExtraBold.ttf", "LiberationSans-Bold.ttf", "arialbd.ttf")
FONT_BOLD = font_path("Montserrat-Bold.ttf", "LiberationSans-Bold.ttf", "arialbd.ttf")
FONT_REG = font_path("Montserrat-Medium.ttf", "LiberationSans-Regular.ttf", "arial.ttf")
_fc = {}


def F(path, size):
    k = (path, int(size))
    if k not in _fc:
        _fc[k] = ImageFont.truetype(path, int(size))
    return _fc[k]


def api(action, **kw):
    r = requests.post(API, json={"action": action, **kw}, headers={"x-reels-worker": TOKEN, "Content-Type": "application/json"}, timeout=90)
    if r.status_code >= 300:
        raise RuntimeError(f"API {action}: HTTP {r.status_code}")
    return r.json()


# ---------------------------------------------------------------- Drehbuch (Claude)
TOOL = {
    "name": "drehbuch",
    "description": "Drehbuch für kurze Social-Media-Reels (9:16) mit Bewegungstext.",
    "input_schema": {
        "type": "object",
        "properties": {
            "farben": {"type": "object", "properties": {
                "hintergrund": {"type": "string", "description": "Hex, eher dunkel oder sehr hell, passend zur Marke"},
                "akzent": {"type": "string", "description": "Hex, kräftige Markenfarbe"},
                "text": {"type": "string", "description": "Hex, gut lesbar auf hintergrund"}},
                "required": ["hintergrund", "akzent", "text"]},
            "logo_bild": {"type": "integer", "description": "Index des hochgeladenen Logos oder -1"},
            "reels": {"type": "array", "items": {"type": "object", "properties": {
                "item_id": {"type": "integer"},
                "typ": {"type": "string", "enum": ["reel", "logo"]},
                "hook": {"type": "string", "description": "max. 6 Wörter, stoppt das Scrollen"},
                "szenen": {"type": "array", "minItems": 2, "maxItems": 5, "items": {"type": "object", "properties": {
                    "text": {"type": "string", "description": "max. 7 Wörter, groß im Bild"},
                    "hervorheben": {"type": "string", "description": "1-2 Wörter aus text, die farbig markiert werden"},
                    "unterzeile": {"type": "string", "description": "optional, max. 10 Wörter"},
                    "bild": {"type": "integer", "description": "Index eines hochgeladenen Bildes oder -1"}},
                    "required": ["text", "bild"]}},
                "cta": {"type": "string", "description": "max. 5 Wörter Handlungsaufforderung"},
                "cta_unterzeile": {"type": "string", "description": "z. B. Website, Ort oder Telefonhinweis aus dem Briefing; nichts erfinden"}},
                "required": ["item_id", "typ", "hook", "szenen", "cta"]}},
        },
        "required": ["farben", "logo_bild", "reels"],
    },
}


def drehbuch(job, n_images):
    items = [{"item_id": it["id"], "titel": it["titel"], "aenderungswunsch": it.get("change_comment") or None} for it in job["items"]]
    system = (
        "Du bist Creative Director einer Social-Media-Agentur (altovate GmbH) und schreibst deutsche Kurzvideo-Drehbücher für Reels, "
        "die nur mit Bewegungstext, Kundenbildern und Musik funktionieren (kein Sprecher). Regeln: kurze, starke Sätze; Du- oder Sie-Form "
        "passend zur Branche (Pflege, Praxen, seriöse Betriebe: Sie); keine erfundenen Zahlen, Preise, Auszeichnungen, Bewertungen oder "
        "Rabatte; keine Heilversprechen; nichts behaupten, was nicht im Briefing steht. Jedes Reel eines Pakets hat einen anderen Blickwinkel "
        "(z. B. Problem/Lösung, Vorteile, Einblick hinter die Kulissen, Einladung/Recruiting, häufige Frage). Titel 'Logo-Animation' = typ 'logo' "
        "(nur hook = Slogan/Firmenname, 2 kurze Szenen, cta). Gibt es einen aenderungswunsch, setze ihn genau um."
    )
    user = (f"Briefing (JSON): {json.dumps(job['briefing'], ensure_ascii=False)}\nFirma: {job['firma']}\n"
            f"Hochgeladene Bilder: {n_images} (Index 0 bis {n_images - 1}; -1 = kein Bild)\n"
            f"Zu erstellende Videos: {json.dumps(items, ensure_ascii=False)}")
    resp = anthropic.Anthropic().messages.create(
        model=config.ANTHROPIC_MODEL, max_tokens=6000, system=system, tools=[TOOL],
        tool_choice={"type": "tool", "name": "drehbuch"}, messages=[{"role": "user", "content": user}])
    return next(b for b in resp.content if b.type == "tool_use").input


# ---------------------------------------------------------------- Bilder / Farben
def hexrgb(h, fallback):
    try:
        h = h.strip().lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except Exception:
        return fallback


def lum(c):
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def load_uploads(job, work):
    imgs = []
    for i, u in enumerate(job["uploads"]):
        try:
            data = requests.get(u["url"], timeout=120).content
            p = work / f"up_{i}"
            p.write_bytes(data)
            try:
                im = Image.open(p)
                im = ImageOps.exif_transpose(im).convert("RGBA")
            except Exception:
                frame = work / f"up_{i}.jpg"
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "1", "-i", str(p), "-frames:v", "1", str(frame)], check=True)
                im = Image.open(frame).convert("RGBA")
            im.thumbnail((2200, 2200))
            imgs.append(im)
        except Exception as e:
            print(f"  Upload {i} übersprungen ({type(e).__name__})")
            imgs.append(None)
    return imgs


def cover(im, w, h, zoom=1.0, dx=0.0, dy=0.0):
    sc = max(w / im.width, h / im.height) * zoom
    nw, nh = int(im.width * sc) + 1, int(im.height * sc) + 1
    r = im.resize((nw, nh), Image.BILINEAR)
    x = int((nw - w) / 2 + dx * (nw - w) / 2)
    y = int((nh - h) / 2 + dy * (nh - h) / 2)
    return r.crop((x, y, x + w, y + h))


def ease(t):
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def ease_io(t):
    t = max(0.0, min(1.0, t))
    return 4 * t ** 3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def wrap(d, text, f, maxw):
    words, lines, cur = text.split(), [], ""
    for w_ in words:
        test = (cur + " " + w_).strip()
        if d.textlength(test, font=f) <= maxw or not cur:
            cur = test
        else:
            lines.append(cur)
            cur = w_
    if cur:
        lines.append(cur)
    return lines


# ---------------------------------------------------------------- Rendern
class Reel:
    def __init__(self, spec, pal, images, logo, firma, seed):
        self.spec, self.images, self.logo, self.firma = spec, images, logo, firma
        self.bg, self.acc, self.txt = pal
        self.rng = random.Random(seed)
        self.blobs = [(self.rng.random(), self.rng.random(), self.rng.uniform(300, 650), self.rng.uniform(0, 6.28)) for _ in range(3)]
        sc = spec["szenen"]
        if spec.get("typ") == "logo":
            self.parts = [("logo", None, 3.2)] + [("scene", s, 2.4) for s in sc[:2]] + [("cta", None, 2.6)]
        else:
            self.parts = [("hook", None, 2.6)] + [("scene", s, 3.0) for s in sc] + [("cta", None, 3.2)]
        self.total = sum(p[2] for p in self.parts)
        self.base = self._background()

    def _background(self):
        im = Image.new("RGB", (W, H), self.bg)
        return im

    def bg_frame(self, t):
        im = self.base.copy()
        ov = Image.new("RGBA", (W // 4, H // 4), (0, 0, 0, 0))
        d = ImageDraw.Draw(ov)
        for i, (bx, by, r, ph) in enumerate(self.blobs):
            x = (bx * W + math.sin(t * 0.5 + ph) * 120) / 4
            y = (by * H + math.cos(t * 0.4 + ph) * 160) / 4
            rr = r / 4
            col = self.acc if i % 2 == 0 else tuple(int(c * 0.6 + 255 * 0.4) for c in self.acc)
            d.ellipse((x - rr, y - rr, x + rr, y + rr), fill=col + (70 if lum(self.bg) < 128 else 55,))
        ov = ov.filter(ImageFilter.GaussianBlur(28)).resize((W, H), Image.BILINEAR)
        im.paste(ov, (0, 0), ov)
        return im

    def kinetic(self, im, text, hl, t, y_center, size=118, color=None, maxw=900):
        d = ImageDraw.Draw(im)
        f = F(FONT_BLACK, size)
        lines = wrap(d, text.upper() if len(text) < 40 else text, f, maxw)
        while (len(lines) > 4 or max(d.textlength(l, font=f) for l in lines) > maxw) and size > 60:
            size -= 6
            f = F(FONT_BLACK, size)
            lines = wrap(d, text.upper() if len(text) < 40 else text, f, maxw)
        lh = int(size * 1.08)
        y0 = y_center - lh * len(lines) / 2
        hlw = {w_.strip(".,!?").lower() for w_ in (hl or "").split()}
        k = 0
        for li, line in enumerate(lines):
            words = line.split()
            lw = d.textlength(line, font=f)
            x = (W - lw) / 2
            for wd in words:
                p = ease((t - 0.08 * k) / 0.45)
                k += 1
                if p <= 0:
                    x += d.textlength(wd + " ", font=f)
                    continue
                ww = d.textlength(wd, font=f)
                yy = y0 + li * lh + (1 - p) * 70
                is_hl = wd.strip(".,!?").lower() in hlw
                if is_hl:
                    pad = 14
                    bw = ww * ease((t - 0.08 * k - 0.1) / 0.35)
                    if bw > 0:
                        d.rounded_rectangle((x - pad, yy + size * 0.12, x - pad + bw + 2 * pad, yy + size * 1.08), radius=14, fill=self.acc)
                col = (color or self.txt) if not is_hl else ((15, 15, 15) if lum(self.acc) > 150 else (255, 255, 255))
                a = int(255 * p)
                layer = Image.new("RGBA", (int(ww) + 40, lh + 40), (0, 0, 0, 0))
                ImageDraw.Draw(layer).text((20, 20), wd, font=f, fill=col + (a,))
                im.paste(layer, (int(x) - 20, int(yy) - 20), layer)
                x += d.textlength(wd + " ", font=f)
        return y0 + lh * len(lines)

    def sub(self, im, text, t, y, color=None):
        if not text:
            return
        d = ImageDraw.Draw(im)
        f = F(FONT_REG, 50)
        p = ease((t - 0.5) / 0.5)
        if p <= 0:
            return
        for i, line in enumerate(wrap(d, text, f, 860)[:3]):
            lw = d.textlength(line, font=f)
            layer = Image.new("RGBA", (int(lw) + 20, 80), (0, 0, 0, 0))
            ImageDraw.Draw(layer).text((10, 5), line, font=f, fill=(color or self.txt) + (int(230 * p),))
            im.paste(layer, (int((W - lw) / 2) - 10, int(y + i * 66 + (1 - p) * 30)), layer)

    def photo(self, im, img, t, dur, idx):
        z = 1.08 + 0.10 * (t / dur)
        dx = (0.5 if idx % 2 else -0.5) * (t / dur)
        ph = cover(img, W, H, z, dx, 0).convert("RGB")
        im.paste(ph, (0, 0))
        grad = Image.new("L", (1, H))
        for y in range(H):
            grad.putpixel((0, y), int(40 + 175 * (y / H) ** 1.4))
        shade = Image.new("RGB", (W, H), (0, 0, 0))
        im.paste(shade, (0, 0), grad.resize((W, H)))

    def logo_paste(self, im, cx, cy, maxw, maxh, p=1.0, rot=0.0):
        if not self.logo or p <= 0:
            return False
        lg = self.logo.copy()
        lg.thumbnail((int(maxw * (0.6 + 0.4 * p)), int(maxh * (0.6 + 0.4 * p))))
        if rot:
            lg = lg.rotate(rot, resample=Image.BICUBIC, expand=True)
        a = lg.split()[-1].point(lambda v: int(v * p))
        lg.putalpha(a)
        plate = Image.new("RGBA", (lg.width + 60, lg.height + 60), (255, 255, 255, int(235 * p)))
        mask = Image.new("L", plate.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, plate.width - 1, plate.height - 1), radius=36, fill=int(235 * p))
        im.paste(plate, (int(cx - plate.width / 2), int(cy - plate.height / 2)), mask)
        im.paste(lg, (int(cx - lg.width / 2), int(cy - lg.height / 2)), lg)
        return True

    def frame(self, tt):
        acc_t = 0.0
        for i, (kind, sc, dur) in enumerate(self.parts):
            if tt < acc_t + dur or i == len(self.parts) - 1:
                t = tt - acc_t
                break
            acc_t += dur
        has_img = kind == "scene" and sc.get("bild", -1) is not None and 0 <= sc.get("bild", -1) < len(self.images) and self.images[sc["bild"]] is not None
        if has_img:
            im = Image.new("RGB", (W, H))
            self.photo(im, self.images[sc["bild"]], t, dur, i)
            tc = (255, 255, 255)
        else:
            im = self.bg_frame(tt)
            tc = None
        d = ImageDraw.Draw(im)
        if kind == "hook":
            d.rounded_rectangle((W / 2 - 60, 560, W / 2 + 60, 572), radius=6, fill=self.acc)
            self.kinetic(im, self.spec["hook"], self.spec["hook"].split()[-1] if self.spec["hook"] else "", t, H * 0.45, size=132)
        elif kind == "logo":
            p = ease(t / 0.9)
            if not self.logo_paste(im, W / 2, H * 0.42, 620, 420, p, rot=(1 - p) * -12):
                self.kinetic(im, self.firma, "", t, H * 0.42, size=120)
            self.kinetic(im, self.spec["hook"], "", t - 0.8, H * 0.62, size=64)
        elif kind == "scene":
            yb = self.kinetic(im, sc["text"], sc.get("hervorheben", ""), t, H * (0.62 if has_img else 0.46), color=tc)
            self.sub(im, sc.get("unterzeile", ""), t, yb + 40, color=tc)
        else:  # cta
            p = ease(t / 0.6)
            top = H * 0.30
            if self.logo_paste(im, W / 2, top, 420, 260, p):
                top += 230
            else:
                f = F(FONT_BOLD, 54)
                lw = d.textlength(self.firma, font=f)
                d.text(((W - lw) / 2, top - 30), self.firma, font=f, fill=self.txt)
                top += 120
            self.kinetic(im, self.spec["cta"], self.spec["cta"].split()[0] if self.spec["cta"] else "", t - 0.2, top + 180, size=104)
            if self.spec.get("cta_unterzeile"):
                f = F(FONT_BOLD, 48)
                txt = self.spec["cta_unterzeile"]
                lw = min(900, d.textlength(txt, font=f))
                pb = ease((t - 0.7) / 0.5)
                if pb > 0:
                    y = top + 420
                    d.rounded_rectangle(((W - lw) / 2 - 40, y, (W + lw) / 2 + 40, y + 100), radius=50, fill=self.acc)
                    d.text(((W - lw) / 2, y + 22), txt, font=f, fill=(15, 15, 15) if lum(self.acc) > 150 else (255, 255, 255))
        # Übergang: Akzent-Wischer am Szenenanfang
        if i > 0 and t < 0.28:
            p = ease_io(t / 0.28)
            x = int(W * p)
            d.rectangle((x, 0, W, H), fill=self.acc)
        # Fortschrittsbalken oben
        d.rectangle((0, 0, int(W * tt / self.total), 10), fill=self.acc)
        return im

    def render(self, out_mp4, music_wav):
        n = int(self.total * FPS)
        p = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
                              "-i", "-", "-i", str(music_wav), "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "medium", "-crf", "19",
                              "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(out_mp4)],
                             stdin=subprocess.PIPE)
        for k in range(n):
            p.stdin.write(self.frame(k / FPS).tobytes())
        p.stdin.close()
        if p.wait() != 0:
            raise RuntimeError("ffmpeg render")


# ---------------------------------------------------------------- Musik (synthetisch, lizenzfrei)
def music(path, seconds, seed):
    rng = random.Random(seed)
    bpm = rng.choice([100, 108, 116, 122])
    beat = 60 / bpm
    n = int((seconds + 1) * SR)
    t = np.arange(n) / SR
    out = np.zeros(n, dtype=np.float32)
    root = rng.choice([-9, -7, -5, -4, -2]) - 12
    prog = rng.choice([[0, -3, -7, -5], [0, -5, -3, -7], [0, 5, -3, -5]])
    minor = rng.random() < 0.4

    def hz(s):
        return 440 * 2 ** (s / 12)

    total_beats = int(seconds / beat) + 2
    for b in range(total_beats):
        s0 = int(b * beat * SR)
        # Kick
        L = int(0.25 * SR)
        tt = np.arange(L) / SR
        kick = np.sin(2 * np.pi * (50 + 90 * np.exp(-tt * 30)) * tt) * np.exp(-tt * 14)
        out[s0:s0 + L] += 0.55 * kick[: max(0, min(L, n - s0))]
        # Hi-Hat auf Achteln
        for off in (0.5,):
            h0 = int((b + off) * beat * SR)
            L2 = int(0.05 * SR)
            if h0 + L2 < n:
                out[h0:h0 + L2] += 0.08 * np.random.default_rng(b).standard_normal(L2) * np.exp(-np.arange(L2) / SR * 80)
        # Clap auf 2 und 4
        if b % 2 == 1:
            L3 = int(0.12 * SR)
            if s0 + L3 < n:
                out[s0:s0 + L3] += 0.12 * np.random.default_rng(1000 + b).standard_normal(L3) * np.exp(-np.arange(L3) / SR * 30)
    # Akkorde + Bass, je 4 Schläge
    bar = 4 * beat
    for k in range(int(seconds / bar) + 2):
        c = root + prog[k % len(prog)]
        third = 3 if minor else 4
        s0 = int(k * bar * SR)
        L = int(bar * SR)
        if s0 >= n:
            break
        L = min(L, n - s0)
        tt = np.arange(L) / SR
        env = np.minimum(1, tt / 0.02) * np.exp(-tt * 1.2)
        chord = sum(np.sin(2 * np.pi * hz(c + 12 + iv) * tt) for iv in (0, third, 7, 12)) / 4
        out[s0:s0 + L] += 0.16 * chord * env
        for q in range(4):
            q0 = int(q * beat * SR)
            Lb = min(int(beat * 0.9 * SR), L - q0)
            if Lb <= 0:
                continue
            tb = np.arange(Lb) / SR
            bass = np.sin(2 * np.pi * hz(c) * tb) * np.exp(-tb * 4)
            out[s0 + q0:s0 + q0 + Lb] += 0.28 * bass
    fade = int(1.2 * SR)
    out[-fade:] *= np.linspace(1, 0, fade)
    out[: int(0.05 * SR)] *= np.linspace(0, 1, int(0.05 * SR))
    out = out / (np.abs(out).max() + 1e-6) * 0.7
    pcm = (out * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


# ---------------------------------------------------------------- Wasserzeichen-Vorschau
def watermark_png(path):
    im = Image.new("RGBA", (720, 1280), (0, 0, 0, 0))
    tile = Image.new("RGBA", (1600, 1600), (0, 0, 0, 0))
    d = ImageDraw.Draw(tile)
    f = F(FONT_BLACK, 54)
    for row in range(0, 1600, 190):
        d.text((-100 + (row // 190 % 2) * 160, row), "VORSCHAU · ALTOVATE   " * 4, font=f, fill=(255, 255, 255, 120))
    tile = tile.rotate(28, resample=Image.BICUBIC)
    im.paste(tile, (-440, -160), tile)
    d2 = ImageDraw.Draw(im)
    d2.rounded_rectangle((180, 1170, 540, 1230), radius=30, fill=(0, 0, 0, 140))
    f2 = F(FONT_BOLD, 30)
    tw = d2.textlength("VORSCHAU", font=f2)
    d2.text(((720 - tw) / 2, 1182), "VORSCHAU", font=f2, fill=(255, 255, 255, 230))
    im.save(path)


def make_preview(final, preview, wm):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(final), "-i", str(wm), "-filter_complex",
                    "[0:v]scale=720:1280[v];[v][1:v]overlay=0:0", "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
                    "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(preview)], check=True)


def put(url, path):
    with open(path, "rb") as f:
        r = requests.put(url, data=f, headers={"Content-Type": "video/mp4", "x-upsert": "true"}, timeout=600)
    if r.status_code >= 300:
        raise RuntimeError(f"Upload HTTP {r.status_code}")


# ---------------------------------------------------------------- Auftrag
def process(job):
    no = job["order_no"]
    print(f"[{no}] {len(job['items'])} Video(s)")
    api("claim", order_id=job["order_id"])
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        images = load_uploads(job, work)
        valid = [i for i, im in enumerate(images) if im is not None]
        spec = drehbuch(job, len(images))
        pal = (hexrgb(spec["farben"].get("hintergrund", ""), (18, 18, 28)), hexrgb(spec["farben"].get("akzent", ""), (168, 85, 247)),
               hexrgb(spec["farben"].get("text", ""), (250, 250, 250)))
        if abs(lum(pal[0]) - lum(pal[2])) < 90:  # Lesbarkeit absichern
            pal = (pal[0], pal[1], (15, 15, 15) if lum(pal[0]) > 128 else (250, 250, 250))
        li = spec.get("logo_bild", -1)
        logo = images[li] if isinstance(li, int) and 0 <= li < len(images) else None
        wm = work / "wm.png"
        watermark_png(wm)
        by_id = {r["item_id"]: r for r in spec["reels"]}
        n = len(job["items"])
        for k, it in enumerate(job["items"]):
            r = by_id.get(it["id"]) or spec["reels"][min(k, len(spec["reels"]) - 1)]
            for sc in r["szenen"]:
                if sc.get("bild", -1) not in valid:
                    sc["bild"] = -1
            reel = Reel(r, pal, images, logo, job["firma"], seed=f"{job['order_id']}-{it['id']}-{it.get('change_comment') or ''}")
            mus = work / f"m{it['id']}.wav"
            music(mus, reel.total, seed=f"{job['order_id']}-{k}")
            final = work / f"{it['id']}.mp4"
            reel.render(final, mus)
            prev = work / f"{it['id']}_v.mp4"
            make_preview(final, prev, wm)
            urls = api("upload_urls", item_id=it["id"])
            put(urls["final"]["url"], final)
            put(urls["preview"]["url"], prev)
            if k + 1 < n:
                api("progress", order_id=job["order_id"], progress=25 + int(55 * (k + 1) / n))
            api("done", item_id=it["id"], preview_path=urls["preview"]["path"], final_path=urls["final"]["path"])
            print(f"  [{no}] Video {k + 1}/{n} fertig ({reel.total:.1f} s)")


def main():
    if not TOKEN:
        print("REELS_WORKER_TOKEN fehlt")
        return 1
    jobs = api("jobs").get("jobs", [])
    print(f"{len(jobs)} Auftrag/Aufträge offen")
    fails = 0
    for job in jobs:
        try:
            process(job)
        except Exception as e:
            fails += 1
            # nur Typ + Zeile loggen (öffentliche Logs!)
            tb = traceback.extract_tb(e.__traceback__)[-1]
            msg = f"{type(e).__name__} in Zeile {tb.lineno}"
            print(f"  [{job['order_no']}] Fehler: {msg}")
            try:
                api("fail", order_id=job["order_id"], error=msg)
            except Exception:
                pass
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
