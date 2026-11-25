"""Animierte KI-Szenenbilder als Short-Hintergrund (Robin 25.11.2026: statt Pexels-Stockvideos,
„so wie bei Was wäre wenn").

- Bilder: Cloudflare Workers AI über die Supabase-Funktion site-bilder (gleiches Modell und gleicher
  Pixar-Stil wie „Was wäre wenn“), hochkant 9:16.
- Bewegung: 2,5D-Kamerafahrt mit Tiefenkarte + Partikel aus waswaerewenn/bewegung.py, weiche Überblendung
  zwischen den Szenen.
- Ergebnis ist ein stummes MP4 in Länge des Sprechtexts; render_segment legt Ton, Untertitel und Cover darüber.
- Fällt die Bild-API aus (weniger als 2 Bilder), gibt build_scene_video None zurück -> Pipeline nimmt
  wie früher Pexels.
"""
import base64
import hashlib
import io
import os
import subprocess
import time
from pathlib import Path

import numpy as np
import requests
from PIL import Image

import config

BILDER_URL = "https://kfiiaqjzzwwacthzrcut.supabase.co/functions/v1/site-bilder"
MODEL = "@cf/leonardo/lucid-origin"
STYLE = ("3D pixar style animation still, expressive characters with clear emotions and body language, "
         "cinematic lighting, vibrant colors, shallow depth of field, vertical composition, highly detailed, "
         "no text, no letters, no watermark, no logo")
IMG_W, IMG_H = 864, 1536          # 9:16, wird für die Kamerafahrt auf Videogröße hochskaliert
XFADE = 0.35                      # Sekunden Überblendung zwischen zwei Szenen
CACHE = config.ASSETS_DIR / "scene_cache"


def _fetch(prompt: str) -> Image.Image | None:
    CACHE.mkdir(parents=True, exist_ok=True)
    full = f"{prompt.strip().rstrip('.')}, {STYLE}"
    p = CACHE / (hashlib.sha1(f"{full}|{IMG_W}x{IMG_H}".encode()).hexdigest()[:16] + ".jpg")
    if p.exists():
        return Image.open(p).convert("RGB")
    tok = os.environ.get("PFLEGE_UPLOAD_TOKEN", "").strip()
    if not tok:
        print("  Szenenbild: PFLEGE_UPLOAD_TOKEN fehlt")
        return None
    for attempt in range(3):
        try:
            r = requests.post(BILDER_URL, json={"prompt": full[:1900], "model": MODEL, "width": IMG_W, "height": IMG_H},
                              headers={"x-pflege-token": tok}, timeout=180).json()
            if "b64" in r:
                im = Image.open(io.BytesIO(base64.b64decode(r["b64"]))).convert("RGB")
                im.save(p, quality=94)
                return im
            print("  Szenenbild-Fehler:", str(r)[:200])
        except Exception as e:  # noqa: BLE001
            print("  Szenenbild-Fehler:", str(e)[:200])
        time.sleep(4 * (attempt + 1))
    return None


def build_scene_video(prompts: list[str], duration: float, out_path: Path,
                      resolution: tuple[int, int] = config.SHORT_RESOLUTION) -> str | None:
    """Rendert die Szenen gleichmäßig verteilt über `duration` Sekunden. None = bitte Pexels nehmen."""
    from waswaerewenn.bewegung import Shot  # nutzt dasselbe Tiefenmodell (Cache ~/.cache/waswaerewenn)

    W, H = resolution
    imgs = [(pr, im) for pr in prompts if pr and (im := _fetch(pr)) is not None]
    if len(imgs) < 2:
        print(f"  Nur {len(imgs)} Szenenbild(er) - nehme Stockvideos")
        return None
    shots = [Shot(im, i, W, H, pr) for i, (pr, im) in enumerate(imgs)]

    fps = config.FPS
    total = duration + 0.3
    n = len(shots)
    per = total / n
    frames = int(total * fps) + 1
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
           "-r", str(fps), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
           str(out_path)]
    ff = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    t_start = time.time()
    try:
        for fi in range(frames):
            t = fi / fps
            si = min(n - 1, int(t / per))
            k = (t - si * per) / per
            fr = shots[si].frame(k, t)
            # weiche Überblendung in die nächste Szene
            nxt_start = (si + 1) * per
            if si + 1 < n and t > nxt_start - XFADE:
                a = (t - (nxt_start - XFADE)) / XFADE
                nx = shots[si + 1].frame(0.0, t)
                fr = Image.blend(fr, nx, max(0.0, min(1.0, a)))
            ff.stdin.write(np.asarray(fr, dtype=np.uint8).tobytes())
    finally:
        ff.stdin.close()
        ff.wait()
    if ff.returncode != 0 or not Path(out_path).exists():
        return None
    print(f"  {n} animierte Szenen gerendert ({time.time() - t_start:.0f} s)")
    return str(out_path)
