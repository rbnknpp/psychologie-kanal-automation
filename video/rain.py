"""Regen-Video zum Einschlafen (Robin 23.10.2026, ersetzt das taegliche Themen-Langvideo):
3 Stunden Regengeraeusche, je nach Szene mit fernem Donner und leiser Musik, dazu ein ruhiges Foto.

Der Regen ist synthetisch (ffmpeg-Rauschen, gefiltert und moduliert), die Musik kommt aus
video.ambient_music, das Foto von Pexels (gewerblich frei). Keine Lizenzfragen.
Jeden Tag eine andere Szene, damit sich Titel, Bild und Klang unterscheiden.
"""
import datetime
import random
import subprocess
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

import config

SAMPLE_RATE = 48000

# name, Pexels-Suche, Tiefpass des Regens (heller/dumpfer), Donner, Musik
SCENES = [
    {"name": "Regen am Fenster", "query": "rain window night", "lp": 7000, "thunder": False, "music": True},
    {"name": "Regen im Wald", "query": "rain forest", "lp": 5500, "thunder": False, "music": False},
    {"name": "Gewitter in der Ferne", "query": "storm clouds night", "lp": 5000, "thunder": True, "music": False},
    {"name": "Regen auf dem Dach", "query": "rain roof", "lp": 4200, "thunder": False, "music": True},
    {"name": "Regennacht in der Stadt", "query": "rainy city night", "lp": 6500, "thunder": False, "music": True},
    {"name": "Regen am See", "query": "rain lake", "lp": 6000, "thunder": False, "music": True},
    {"name": "Herbstregen", "query": "autumn rain leaves", "lp": 5800, "thunder": False, "music": True},
    {"name": "Regen an der Berghütte", "query": "cabin rain mountains", "lp": 4800, "thunder": True, "music": True},
    {"name": "Sanfter Landregen", "query": "rain field countryside", "lp": 6200, "thunder": False, "music": False},
    {"name": "Regen und Kaminfeuer", "query": "rain cozy fireplace", "lp": 4500, "thunder": False, "music": True},
]


def scene_for(day: datetime.date) -> dict:
    return SCENES[day.toordinal() % len(SCENES)]


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, capture_output=True)


def fetch_photo(query: str, out: Path, seed: int) -> bool:
    """Querformat-Foto von Pexels holen. False, wenn kein Schluessel oder kein Treffer."""
    if not config.PEXELS_API_KEY:
        return False
    try:
        r = requests.get("https://api.pexels.com/v1/search", headers={"Authorization": config.PEXELS_API_KEY},
                         params={"query": query, "orientation": "landscape", "per_page": 15}, timeout=30)
        photos = r.json().get("photos", [])
        if not photos:
            return False
        pick = photos[seed % len(photos)]
        img = requests.get(pick["src"]["large2x"], timeout=60)
        img.raise_for_status()
        out.write_bytes(img.content)
        return True
    except Exception as e:  # noqa: BLE001 - Foto ist optional, es gibt einen Ersatz
        print(f"  Pexels-Foto nicht geladen ({e}), nehme Ersatzbild")
        return False


def build_still(scene: dict, hours: int, work: Path) -> tuple[Path, Path]:
    """Videobild (ruhig, abgedunkelt) und Thumbnail (mit grosser Schrift)."""
    raw = work / "foto.jpg"
    seed = datetime.date.today().toordinal()
    if fetch_photo(scene["query"], raw, seed):
        img = Image.open(raw).convert("RGB")
        w, h = img.size
        tw = min(w, int(h * 16 / 9))
        th = int(tw * 9 / 16)
        img = img.crop(((w - tw) // 2, (h - th) // 2, (w + tw) // 2, (h + th) // 2)).resize((1920, 1080), Image.LANCZOS)
    else:
        img = Image.new("RGB", (1920, 1080), (14, 20, 34))
        d = ImageDraw.Draw(img)
        rng = random.Random(seed)
        for _ in range(900):
            x, y = rng.randint(0, 1920), rng.randint(0, 1080)
            d.line([(x, y), (x - 6, y + rng.randint(18, 40))], fill=(90, 110, 150), width=1)
        img = img.filter(ImageFilter.GaussianBlur(1.2))
    img = ImageEnhance.Brightness(img).enhance(0.62)
    img = ImageEnhance.Color(img).enhance(0.8)
    video_still = work / "still.jpg"
    img.save(video_still, quality=92)

    thumb = img.copy()
    d = ImageDraw.Draw(thumb)
    try:
        font_file = "C:/Windows/Fonts/segoeuib.ttf" if Path("C:/Windows/Fonts/segoeuib.ttf").exists() else "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
        big = ImageFont.truetype(font_file, 150)
        small = ImageFont.truetype(font_file, 70)
    except OSError:
        big = small = ImageFont.load_default()
    d.text((110, 640), f"{hours} STD REGEN", font=big, fill=(240, 244, 255), stroke_width=6, stroke_fill=(10, 14, 24))
    d.text((115, 820), scene["name"], font=small, fill=(190, 210, 240), stroke_width=4, stroke_fill=(10, 14, 24))
    thumb_path = work / "thumbnail.jpg"
    thumb.save(thumb_path, quality=92)
    return video_still, thumb_path


def render_rain(still: Path, out: Path, total_seconds: int, scene: dict, music: Path | None) -> Path:
    """Regen = rosa Rauschen (Grundrauschen) + gefiltertes weisses Rauschen mit Flattern (Tropfen),
    optional tiefes, langsam an- und abschwellendes Grollen (Donner) und leise Musikschleife."""
    T = int(total_seconds)
    parts = [
        f"anoisesrc=color=pink:amplitude=0.6:sample_rate={SAMPLE_RATE}:duration={T},highpass=f=250,lowpass=f={scene['lp']},volume=0.55,aformat=channel_layouts=stereo[rain]",
        f"anoisesrc=color=white:amplitude=0.35:sample_rate={SAMPLE_RATE}:duration={T}:seed=7,highpass=f=1800,lowpass=f={scene['lp'] + 1500},tremolo=f=6:d=0.55,volume=0.12,aformat=channel_layouts=stereo[drops]",
    ]
    labels = ["[rain]", "[drops]"]
    if scene["thunder"]:
        parts.append(f"anoisesrc=color=brown:amplitude=0.8:sample_rate={SAMPLE_RATE}:duration={T}:seed=3,lowpass=f=110,volume='0.9*pow(max(0,sin(2*PI*t/47)*sin(2*PI*t/13)),2)':eval=frame,aformat=channel_layouts=stereo[thunder]")
        labels.append("[thunder]")
    inputs = ["-loop", "1", "-framerate", "2", "-i", str(still)]
    if music:
        inputs += ["-stream_loop", "-1", "-i", str(music)]
        parts.append(f"[1:a]aresample={SAMPLE_RATE},aformat=channel_layouts=stereo,volume=0.32[music]")
        labels.append("[music]")
    fade_out = max(0, T - 90)
    parts.append(f"{''.join(labels)}amix=inputs={len(labels)}:duration=first:normalize=0,volume=2.8,"
                 f"afade=t=in:d=8,afade=t=out:st={fade_out}:d=90,alimiter=limit=0.85[a]")
    _run(["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(parts),
          "-map", "0:v", "-map", "[a]", "-t", str(T),
          "-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage", "-crf", "28", "-pix_fmt", "yuv420p", "-r", "2",
          "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(out)])
    return out
