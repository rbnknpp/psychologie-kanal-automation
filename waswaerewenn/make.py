"""Kanal „Was wäre wenn" (@Waswärewenn97, seit 17.11.2026): animierte Gedankenexperimente im 3D-Cartoon-Look.

Ablauf je Folge (waswaerewenn/folgen/NNN_*.json):
  Stimme (edge-tts, Wortzeiten) -> Bilder je Szene (Cloudflare Lucid Origin 1536x864 über Edge-Funktion site-bilder)
  -> Bewegung (Kamerafahrten, Überblendungen), große Texteinblendungen, Untertitel, leise Musik -> video.mp4 + thumb.jpg

Aufruf:  python -m waswaerewenn.make waswaerewenn/folgen/001_erde_stoppt.json [--upload]
"""
import argparse
import base64
import hashlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import pflege.tiktok_news as _pt  # noqa: E402
from pflege.tiktok_news import align, chunks, font, tts, wrap  # noqa: E402
_pt.RATE = os.getenv("WWW_RATE", "+0%")  # erzählend, etwas ruhiger als die TikToks

HERE = Path(__file__).resolve().parent
OUT = ROOT / "output" / "waswaerewenn"
W, H, FPS = 1920, 1080, 30
BILDER = "https://kfiiaqjzzwwacthzrcut.supabase.co/functions/v1/site-bilder"
MODEL = "@cf/leonardo/lucid-origin"
STYLE = "3D pixar style animation still, vibrant colors, cinematic lighting, highly detailed, no text, no letters, no watermark"
YELLOW, WHITE, BLACK = (255, 214, 70), (255, 255, 255), (0, 0, 0)
XFADE = 0.45


def prompt_for(scene_bild, figur):
    p = scene_bild.replace("Leo,", f"{figur},").replace("Leo ", f"{figur} ", 1) if "Leo" in scene_bild else scene_bild
    return f"{p}, {STYLE}"


def fetch(prompt, cache, w=1536, h=864):
    cache.mkdir(parents=True, exist_ok=True)
    p = cache / (hashlib.sha1(f"{prompt}|{w}x{h}".encode()).hexdigest()[:16] + ".jpg")
    if p.exists():
        return Image.open(p).convert("RGB")
    tok = os.environ.get("PFLEGE_UPLOAD_TOKEN", "").strip()
    for attempt in range(4):
        try:
            r = requests.post(BILDER, json={"prompt": prompt[:1900], "model": MODEL, "width": w, "height": h},
                              headers={"x-pflege-token": tok}, timeout=180).json()
            if "b64" in r:
                im = Image.open(io.BytesIO(base64.b64decode(r["b64"]))).convert("RGB")
                im.save(p, quality=94)
                return im
            print("  Bild-Fehler:", str(r)[:200])
        except Exception as e:  # noqa: BLE001
            print("  Bild-Fehler:", e)
    return None


def outlined(d, xy, text, f, fill, stroke=8, anchor="mm"):
    d.text(xy, text, font=f, fill=fill, stroke_width=stroke, stroke_fill=BLACK, anchor=anchor)


def title_layer(text):
    lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    size = 150
    f = font(size, "ExtraBold")
    while d.textlength(text, font=f) > W - 240 and size > 70:
        size -= 10
        f = font(size, "ExtraBold")
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(glow).text((W / 2, 230), text, font=f, fill=(255, 160, 0, 200), anchor="mm")
    lay.alpha_composite(glow.filter(ImageFilter.GaussianBlur(25)))
    outlined(d, (W / 2, 230), text, f, YELLOW, stroke=10)
    return lay


def caption_layer(text):
    lay = Image.new("RGBA", (W, 200), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    outlined(d, (W / 2, 100), text.upper(), font(64, "ExtraBold"), WHITE, stroke=7)
    return lay


def motion(idx, k):
    """Kamerafahrt je Bild (k = 0..1 über die Standzeit): abwechselnd Zoom rein/raus und Schwenk."""
    m = idx % 4
    if m == 0:
        return 1.0 + 0.10 * k, 0.5, 0.5
    if m == 1:
        return 1.10 - 0.10 * k, 0.5, 0.45
    if m == 2:
        return 1.08, 0.35 + 0.3 * k, 0.5
    return 1.08, 0.65 - 0.3 * k, 0.55


def frame_from(big, z, cx, cy):
    bw, bh = big.size
    cw, ch = bw / z, bh / z
    x0 = (bw - cw) * cx
    y0 = (bh - ch) * cy
    return big.resize((W, H), Image.BILINEAR, box=(x0, y0, x0 + cw, y0 + ch))


def render(ep, wd):
    wd.mkdir(parents=True, exist_ok=True)
    scenes = ep["szenen"]
    words = tts(" ".join(s["say"].strip() for s in scenes), wd / "voice.mp3")
    align(scenes, words)
    total = scenes[-1]["t1"] + 1.2
    # Bilder laden (gleich = vorheriges Bild weiterführen)
    groups, cur = [], None
    for i, sc in enumerate(scenes):
        b = (sc.get("bild") or "gleich").strip()
        if b != "gleich" or cur is None:
            im = fetch(prompt_for(b, ep["figur"]), wd / "img")
            if im is None:
                raise RuntimeError(f"Bild fehlt (Szene {i + 1}): {b[:60]}")
            cur = {"img_src": im, "t0": sc["t0"], "scenes": []}
            groups.append(cur)
        cur["scenes"].append(i)
        sc["_g"] = len(groups) - 1
    for gi, g in enumerate(groups):
        g["t1"] = groups[gi + 1]["t0"] if gi + 1 < len(groups) else total
        g["big"] = g["img_src"].resize((int(W * 1.12), int(H * 1.12)), Image.LANCZOS)
    titles = {i: title_layer(sc["titel"]) for i, sc in enumerate(scenes) if sc.get("titel")}
    caps = [(a, z, caption_layer(t)) for sc in scenes for a, z, t in chunks(sc["words"], maxw=4, maxc=28)]
    music = wd / "music.wav"
    try:
        from video.ambient_music import build_music_loop
        build_music_loop(music, seed=ep["titel"], minutes=total / 60 + 0.3)
    except Exception as e:  # noqa: BLE001
        print("  Musik übersprungen:", e)
        music = None
    vid = wd / "video_silent.mp4"
    ff = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                           "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                           "-pix_fmt", "yuv420p", str(vid)], stdin=subprocess.PIPE)
    gi = si = ci = 0
    for fi in range(int(total * FPS)):
        t = fi / FPS
        while gi + 1 < len(groups) and t >= groups[gi + 1]["t0"]:
            gi += 1
        while si + 1 < len(scenes) and t >= scenes[si + 1]["t0"]:
            si += 1
        g = groups[gi]
        k = (t - g["t0"]) / max(0.1, g["t1"] - g["t0"] + XFADE)
        fr = frame_from(g["big"], *motion(gi, k))
        if gi > 0 and t - g["t0"] < XFADE:  # Überblendung vom vorigen Bild
            p = groups[gi - 1]
            kp = (t - p["t0"]) / max(0.1, p["t1"] - p["t0"] + XFADE)
            fr = Image.blend(frame_from(p["big"], *motion(gi - 1, kp)), fr, (t - g["t0"]) / XFADE)
        fr = fr.convert("RGBA")
        if si in titles:
            ts = t - scenes[si]["t0"]
            lay = titles[si]
            if ts < 0.25:  # Pop-in
                s = 0.7 + 0.3 * (ts / 0.25)
                sm = lay.resize((int(W * s), int(H * s)), Image.BILINEAR)
                sm.putalpha(sm.split()[3].point(lambda v: int(v * min(1, ts / 0.15))))
                fr.alpha_composite(sm, (int(W / 2 - W * s / 2), int(230 - 230 * s)))
            else:
                fr.alpha_composite(lay)
        while ci + 1 < len(caps) and t >= caps[ci + 1][0] - 0.05:
            ci += 1
        a, z, cl = caps[ci]
        if a - 0.05 <= t < z + 0.3:
            fr.alpha_composite(cl, (0, H - 260))
        ff.stdin.write(fr.convert("RGB").tobytes())
        if fi % (FPS * 30) == 0:
            print(f"  {t:.0f}/{total:.0f} s", flush=True)
    ff.stdin.close()
    ff.wait()
    out = wd / "video.mp4"
    inputs = ["-i", str(vid), "-i", str(wd / "voice.mp3")]
    if music and music.exists():
        inputs += ["-stream_loop", "-1", "-i", str(music)]
        af = ("[1:a]aformat=sample_rates=44100:channel_layouts=stereo[v];"
              "[2:a]aformat=sample_rates=44100:channel_layouts=stereo,volume=0.12[m];[v][m]amix=inputs=2:duration=first:normalize=0[a]")
    else:
        af = "[1:a]aformat=sample_rates=44100:channel_layouts=stereo[a]"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", af, "-map", "0:v", "-map", "[a]",
                    "-t", f"{total:.2f}", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)], check=True)
    return out, total


def thumbnail(ep, wd):
    im = fetch(prompt_for(ep["thumb_bild"], ep["figur"]) + ", expressive face, close-up, high contrast", wd / "img", 1536, 864)
    im = im.resize((1280, 720), Image.LANCZOS).convert("RGBA")
    d = ImageDraw.Draw(im)
    text = ep["thumb_text"]
    size = 130
    f = font(size, "ExtraBold")
    while d.textlength(text, font=f) > 1180 and size > 60:
        size -= 8
        f = font(size, "ExtraBold")
    lines = wrap(d, text, f, 700) if d.textlength(text, font=f) > 700 else [text]
    y = 120
    for ln in lines:
        d.text((60, y), ln, font=f, fill=YELLOW, stroke_width=12, stroke_fill=BLACK)
        y += int(size * 1.05)
    p = wd / "thumb.jpg"
    im.convert("RGB").save(p, quality=92)
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folge")
    ap.add_argument("--upload", action="store_true")
    a = ap.parse_args()
    ep = json.loads(Path(a.folge).read_text(encoding="utf-8"))
    wd = OUT / Path(a.folge).stem
    path, secs = render(ep, wd)
    thumb = thumbnail(ep, wd)
    print(f"fertig: {path} ({secs / 60:.1f} min), Titelbild {thumb}")


if __name__ == "__main__":
    main()
