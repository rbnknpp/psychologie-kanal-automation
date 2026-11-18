"""Bewegung für „Was wäre wenn": 2,5D-Kamerafahrten mit Tiefenkarte + passende Partikel-Effekte.

- Tiefe je Bild: Depth Anything V2 Small (ONNX, CPU, ~1 s/Bild). Fällt das Modell aus, nimmt eine einfache
  Ersatz-Tiefe (unten nah, oben fern) – das Video wird trotzdem fertig.
- Kamera: Zoom/Schwenk wie bisher, zusätzlich verschieben sich nahe Bildteile stärker als ferne (Parallaxe).
- Effekte nach Stichwörtern im Bild-Prompt: Schnee, Regen, Glut/Funken (+ Wackeln), Sterne, sonst Schwebeteilchen.
"""
import os
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

MODEL_URL = "https://huggingface.co/onnx-community/depth-anything-v2-small/resolve/main/onnx/model.onnx"
MODEL_PATH = Path(os.getenv("WWW_DEPTH_MODEL", Path.home() / ".cache" / "waswaerewenn" / "depth_anything_v2_small.onnx"))
PARALLAX = 0.035   # max. Verschiebung naher Teile relativ zur Bildbreite
_sess = None
_failed = False


def _session():
    global _sess, _failed
    if _sess is not None or _failed:
        return _sess
    try:
        import onnxruntime as ort
        if not MODEL_PATH.exists():
            import requests
            MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = MODEL_PATH.with_suffix(".part")
            with requests.get(MODEL_URL, stream=True, timeout=300) as r:
                r.raise_for_status()
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
            tmp.replace(MODEL_PATH)
        _sess = ort.InferenceSession(str(MODEL_PATH), providers=["CPUExecutionProvider"])
    except Exception as e:  # noqa: BLE001
        print("  Tiefenmodell nicht verfügbar, Ersatz-Tiefe:", str(e)[:200])
        _failed = True
    return _sess


def depth_map(img, size):
    """Tiefe 0 (fern) .. 1 (nah) in Größe size=(w, h), geglättet."""
    w, h = size
    s = _session()
    d = None
    if s is not None:
        try:
            x = np.asarray(img.convert("RGB").resize((518, 294), Image.BICUBIC), np.float32) / 255.0
            x = (x - [0.485, 0.456, 0.406]) / [0.229, 0.224, 0.225]
            out = s.run(None, {"pixel_values": x.transpose(2, 0, 1)[None].astype(np.float32)})[0][0]
            lo, hi = np.percentile(out, 2), np.percentile(out, 98)
            d = np.clip((out - lo) / max(1e-6, hi - lo), 0, 1)
        except Exception as e:  # noqa: BLE001
            print("  Tiefe fehlgeschlagen:", str(e)[:120])
    if d is None:
        d = np.tile(np.linspace(0.15, 0.9, 64)[:, None], (1, 64))
    dm = Image.fromarray((d * 255).astype(np.uint8)).resize((w, h), Image.BICUBIC).filter(ImageFilter.GaussianBlur(6))
    return np.asarray(dm, np.float32) / 255.0


# ---------------------------------------------------------------- Effekte
EFFECTS = [
    ("schnee", ("snow", "ice", "frozen", "winter", "blizzard", "glacier", "freez")),
    ("regen", ("rain", "storm", "flood", "thunder")),
    ("glut", ("fire", "explosion", "explod", "lava", "volcano", "burning", "flame", "meteor", "impact", "asteroid")),
    ("sterne", ("space", "galaxy", "orbit", "universe", "stars", "planet", "moon", "astronaut", "black hole")),
]


def effect_for(prompt):
    p = (prompt or "").lower()
    for name, keys in EFFECTS:
        if any(k in p for k in keys):
            return name
    return "staub"


class Particles:
    def __init__(self, kind, W, H, seed):
        self.kind, self.W, self.H = kind, W, H
        rnd = random.Random(seed)
        n = {"schnee": 220, "regen": 260, "glut": 120, "sterne": 90, "staub": 70}[kind]
        self.p = [(rnd.random(), rnd.random(), rnd.uniform(0.4, 1.0), rnd.random() * 6.28) for _ in range(n)]

    def layer(self, t):
        W, H = self.W, self.H
        lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(lay)
        for x0, y0, z, ph in self.p:
            if self.kind == "schnee":
                x = (x0 * W + 40 * np.sin(t * 0.8 + ph) + t * 15 * z) % W
                y = (y0 * H + t * 90 * z) % H
                r = 1.5 + 3 * z
                d.ellipse((x - r, y - r, x + r, y + r), fill=(255, 255, 255, int(150 + 90 * z)))
            elif self.kind == "regen":
                x = (x0 * W - t * 260 * z) % W
                y = (y0 * H + t * 1400 * z) % H
                d.line((x, y, x - 6 * z, y + 34 * z), fill=(200, 215, 255, int(70 + 80 * z)), width=2)
            elif self.kind == "glut":
                x = (x0 * W + 25 * np.sin(t * 1.7 + ph)) % W
                y = H - ((y0 * H + t * 120 * z) % (H * 1.1))
                r = 1.5 + 2.5 * z
                a = int(200 * (0.5 + 0.5 * np.sin(t * 6 + ph)))
                d.ellipse((x - r, y - r, x + r, y + r), fill=(255, 150 + int(70 * z), 40, a))
            elif self.kind == "sterne":
                x, y = x0 * W, y0 * H * 0.75
                a = int(40 + 180 * max(0, np.sin(t * 2.2 * z + ph)) ** 3)
                r = 1 + 1.6 * z
                d.ellipse((x - r, y - r, x + r, y + r), fill=(255, 255, 230, a))
            else:  # staub: schwebende Lichtteilchen
                x = (x0 * W + t * 12 * z + 30 * np.sin(t * 0.5 + ph)) % W
                y = (y0 * H - t * 8 * z + 20 * np.cos(t * 0.4 + ph)) % H
                r = 1.5 + 2.5 * z
                d.ellipse((x - r, y - r, x + r, y + r), fill=(255, 245, 210, int(40 + 70 * z)))
        return lay


def shake(kind, t):
    """Leichtes Wackeln bei Explosionen/Einschlägen (Pixel)."""
    if kind != "glut":
        return 0.0, 0.0
    return 3.0 * np.sin(t * 37.0) * np.sin(t * 3.1), 2.0 * np.sin(t * 29.0 + 1.3) * np.sin(t * 2.3)


# ---------------------------------------------------------------- Kamera
class Shot:
    """Ein Bild mit Tiefe; frame(k) liefert das bewegte Bild (k = 0..1 über die Standzeit)."""

    def __init__(self, img, idx, W, H, prompt=""):
        import cv2  # noqa: F401  (früh prüfen)
        self.W, self.H, self.idx = W, H, idx
        self.big = np.asarray(img.convert("RGB").resize((int(W * 1.16), int(H * 1.16)), Image.LANCZOS))
        bh, bw = self.big.shape[:2]
        self.depth = depth_map(img, (bw, bh))
        self.kind = effect_for(prompt)
        self.fx = Particles(self.kind, W, H, seed=idx * 7919 + len(prompt))
        # Verschiebungsfelder werden in Viertel-Auflösung berechnet und hochskaliert (glatt, ~10x schneller)
        self.sw, self.sh = W // 4, H // 4
        gy, gx = np.mgrid[0:self.sh, 0:self.sw].astype(np.float32)
        self.gx, self.gy = (gx + 0.5) / self.sw, (gy + 0.5) / self.sh
        rnd = random.Random(idx)
        # Bewegungsart je Bild abwechselnd: Dolly rein, Dolly raus, Schwenk links/rechts, Kran hoch
        self.mode = idx % 5
        self.sign = 1 if rnd.random() < 0.5 else -1

    def params(self, k):
        e = k * k * (3 - 2 * k)  # weich ein- und auslaufen
        m = self.mode
        if m == 0:
            return 1.02 + 0.07 * e, 0.5, 0.5, 0.0, 0.0, 0.6 * e        # rein (Tiefenzoom)
        if m == 1:
            return 1.09 - 0.07 * e, 0.5, 0.48, 0.0, 0.0, -0.6 * e      # raus
        if m == 2:
            return 1.06, 0.38 + 0.24 * e, 0.5, (e - 0.5) * 2, 0.0, 0.0   # Schwenk mit Parallaxe
        if m == 3:
            return 1.06, 0.62 - 0.24 * e, 0.5, -(e - 0.5) * 2, 0.0, 0.0
        return 1.06, 0.5, 0.62 - 0.22 * e, 0.0, -(e - 0.5) * 2, 0.2 * e  # Kran hoch

    def frame(self, k, t):
        import cv2
        z, cx, cy, px, py, dz = self.params(min(1.0, max(0.0, k)))
        bh, bw = self.big.shape[:2]
        cw, ch = bw / z, bh / z
        x0, y0 = (bw - cw) * cx, (bh - ch) * cy
        sx, sy = shake(self.kind, t)
        mx = x0 + self.gx * cw + sx
        my = y0 + self.gy * ch + sy
        # Tiefe an der Zielstelle (Näherung) -> nahe Teile stärker verschieben
        dd = cv2.remap(self.depth, mx.astype(np.float32), my.astype(np.float32), cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        amp = PARALLAX * bw
        mx = mx - dd * amp * px * 0.5 - (self.gx - 0.5) * dd * amp * dz
        my = my - dd * amp * py * 0.3 - (self.gy - 0.5) * dd * amp * dz * (bh / bw)
        mx = cv2.resize(mx.astype(np.float32), (self.W, self.H), interpolation=cv2.INTER_LINEAR)
        my = cv2.resize(my.astype(np.float32), (self.W, self.H), interpolation=cv2.INTER_LINEAR)
        out = cv2.remap(self.big, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        fr = Image.fromarray(out).convert("RGBA")
        fr.alpha_composite(self.fx.layer(t))
        return fr.convert("RGB")
