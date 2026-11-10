"""Tagesproduktion fuer den Kanal „Pflege mit Plan“ (@pflege-mit-plan) – eingerichtet 09.11.2026.

Ablauf: Thema waehlen -> Skript (Claude, Fakten fuer Angehoerige NUR von pflege-mit-plan.com)
-> Piper-Stimme je Szene -> animierte Szenen (PIL + ffmpeg, Zoom/Ueberblendung, Untertitel)
-> Upload (geplant) -> Zustand in pflege/state.json.

Taeglich 2 Shorts (je 1 Angehoerige + 1 Azubi), Mo/Mi/Fr zusaetzlich 1 Erklaervideo (3-8 Min).
PFLEGE_REVIEW=1 -> Uploads nur privat (zur Sichtung durch Robin), ohne Veroeffentlichungstermin.

Aufruf:  python -m pflege.make            (Tageslauf)
         python -m pflege.make --test     (1 Short, nur rendern, kein Upload)
"""
import argparse
import copy
import datetime as dt
import html
import json
import math
import os
import random
import re
import subprocess
import sys
import textwrap
import wave
from pathlib import Path
from zoneinfo import ZoneInfo

import anthropic
import requests
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import config  # noqa: E402
from pflege.topics import alle, SITE  # noqa: E402
from tts import piper_engine  # noqa: E402
from video.ambient_music import build_music_loop  # noqa: E402

STATE = Path(__file__).resolve().parent / "state.json"
OUT = ROOT / "output" / "pflege"
TZ = ZoneInfo("Europe/Berlin")
REVIEW = os.getenv("PFLEGE_REVIEW", "1") == "1"
LONG_DAYS = {0, 2, 4}  # Mo, Mi, Fr
SHORT_TIMES = ["12:30", "18:30"]
LONG_TIME = "17:00"
LONG_MIN_SECONDS = 600  # Regel Robin 09.11.2026: Longform immer mindestens 10 Minuten
LONG_MIN_WORDS = 1500  # ca. 10,5 Min. mit Piper (thorsten, length_scale 1.05)

GREEN = (31, 95, 74); SAND = (247, 242, 234); ORANGE = (224, 123, 57); DARK = (24, 38, 32); MINT = (226, 238, 231)
FONT_B = next((p for p in ["/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", r"C:\Windows\Fonts\segoeuib.ttf"] if Path(p).exists()), None)
FONT_R = next((p for p in ["/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", r"C:\Windows\Fonts\segoeui.ttf"] if Path(p).exists()), None)

DISCLAIMER = "Keine Rechts- oder medizinische Beratung. Inhalte ersetzen keine Ausbildung, Pflegekasse oder ärztliche Beratung."


# ---------------------------------------------------------------- Zustand / Themen
def load_state():
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {"used": {}, "titles": []}


def pick(reihe, st, rng):
    pool = [t for t in alle() if t["reihe"] == reihe]
    least = min(st["used"].get(t["slug"], 0) for t in pool)
    cands = [t for t in pool if st["used"].get(t["slug"], 0) == least]
    return rng.choice(cands)


def page_text(url):
    try:
        r = requests.get(url, timeout=30, headers={"User-Agent": "PflegeMitPlanBot/1.0"})
        t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", r.text, flags=re.S)
        t = html.unescape(re.sub(r"<[^>]+>", " ", t))
        return re.sub(r"\s+", " ", t).strip()[:14000]
    except Exception as e:  # noqa: BLE001
        print("Seite nicht lesbar:", url, e)
        return ""


# ---------------------------------------------------------------- Skript
TOOL = {
    "name": "skript",
    "description": "Fertiges Video-Skript",
    "input_schema": {
        "type": "object",
        "properties": {
            "titel": {"type": "string", "description": "YouTube-Titel, max. 90 Zeichen, neugierig, ehrlich, ohne Clickbait-Lügen"},
            "beschreibung": {"type": "string", "description": "2-4 Sätze worum es geht"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "szenen": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "tafel": {"type": "string", "description": "Kernaussage auf dem Bildschirm, max. 6 Wörter"},
                        "unter": {"type": "string", "description": "Unterzeile, max. 10 Wörter"},
                        "sprecher": {"type": "string", "description": "Gesprochener Text dieser Szene, 1-3 kurze Sätze"},
                    },
                    "required": ["tafel", "unter", "sprecher"],
                },
            },
        },
        "required": ["titel", "beschreibung", "tags", "szenen"],
    },
}


TOOL_LONG = copy.deepcopy(TOOL)
TOOL_LONG["input_schema"]["properties"]["szenen"]["items"]["properties"]["sprecher"]["description"] = (
    "Gesprochener Text dieser Szene, 55-75 Wörter (4-6 Sätze), erklärend mit Beispiel")


LONG_PARTS = [
    "Einstieg mit einem typischen Alltagsproblem, warum das Thema wichtig ist, Grundlagen und Begriffe einfach erklärt",
    "Schritt für Schritt das konkrete Vorgehen, mit Alltagsbeispielen und was man dabei beachten muss",
    "Typische Fehler und wie man sie vermeidet, Praxistipps, kurze Zusammenfassung der wichtigsten Punkte und Abschluss",
]


def long_script(topic, avoid, hint=""):
    """Langvideo in 3 Teilen schreiben (je 10 Szenen, ca. 650 Wörter) - ein einzelner Aufruf wird zu kurz."""
    data, done = None, []
    for k, inhalt in enumerate(LONG_PARTS):
        note = (f"Dies ist TEIL {k + 1} VON 3 eines Erklärvideos von mindestens 10 Minuten. Inhalt dieses Teils: {inhalt}.\n"
                + (f"Bereits behandelt (nicht wiederholen): {'; '.join(done)}\n" if done else ""))
        part = {"first": k == 0, "last": k == len(LONG_PARTS) - 1, "note": note}
        d = script(topic, "long", avoid, hint=hint, n_scenes=10, words="620-720", part=part)
        done += [sz.get("tafel", "") for sz in d["szenen"]]
        if data is None:
            data = d
        else:
            data["szenen"] += d["szenen"]
    return data


def script(topic, kind, avoid, hint="", n_scenes=None, words=None, part=None):
    if n_scenes is None:
        n_scenes, words = (6, "130-160") if kind == "short" else (30, "1700-1950")
    if topic["reihe"] == "angehoerige":
        facts = page_text(topic["quelle"])
        rules = (
            "Zielgruppe: pflegende Angehörige. Anrede „Sie“, warm, einfach, ohne Fachchinesisch.\n"
            "FAKTEN-REGEL: Verwende Beträge, Fristen, Paragrafen und Regeln AUSSCHLIESSLICH aus dem folgenden Seitentext. "
            "Steht etwas nicht darin, lass es weg. Keine eigenen Zahlen.\n"
            f"SEITENTEXT ({topic['quelle']}):\n{facts}\n"
        )
        cta = f"Letzte Szene: Hinweis, dass alle Details und Werkzeuge auf pflege-mit-plan.com stehen."
    else:
        rules = (
            "Zielgruppe: Auszubildende in der Pflege – generalistische Ausbildung Pflegefachfrau/-mann UND Pflegeassistenz/Pflegefachassistenz. Anrede „du“, locker, klar, prüfungsnah. Wo sich Aufgaben unterscheiden, kurz sagen, was die Assistenz übernimmt und was der Fachkraft vorbehalten ist (ohne landesspezifische Details zu erfinden).\n"
            "Nur gesichertes Lehrbuch-Grundwissen. KEINE Medikamente, Wirkstoffe, Dosierungen, Grenzwerte für Therapieentscheidungen "
            "oder ärztlichen Entscheidungen. Bei Normalwerten nur allgemein anerkannte Erwachsenen-Richtwerte und den Hinweis, "
            "dass Einrichtungsstandards und die Praxisanleitung gelten. Keine erfundenen Studien, Zahlen oder Zitate.\n"
        )
        cta = "Letzte Szene: Abo-Hinweis für die Reihe „Pflege lernen“ und Verweis auf das Pflege-Lexikon auf pflege-mit-plan.com."
    if part and not part["last"]:
        cta = "KEIN Abschluss, keine Verabschiedung, kein Abo-Hinweis – das Video geht im nächsten Teil weiter."
    system = (
        "Du schreibst deutsche Erklärvideo-Skripte für den YouTube-Kanal „Pflege mit Plan“ (Ratgeber aus dem Pflegealltag, "
        "geprüft von einer Pflegedienstleitung). Nenne niemals einen Arbeitgeber, keine Personen-Namen, keine Patientendaten. "
        "Kein „kostenlos“ bei der Pflegebox – stattdessen „ohne Zuzahlung“ mit Bedingung. Keine Heilversprechen.\n" + rules
    )
    user = (
        f"Thema: {topic['titel']}\nFormat: {'YouTube-Short (60-75 Sekunden)' if kind == 'short' else 'Erklärvideo (mindestens 10, ideal 11-13 Minuten)'}\n"
        + (part["note"] if part else "")
        + f"Genau {n_scenes} Szenen, insgesamt {words} gesprochene Wörter. "
        + ("Erste Szene = starker Einstieg mit Alltagsproblem.\n" if not part or part["first"] else "")
        + f"{cta}\n"
        + (("Für die Länge: Vertiefe mit Alltagsbeispielen, typischen Fehlern, Schritt-für-Schritt-Erklärungen und kurzen Zusammenfassungen – "
            "aber ohne neue Zahlen oder Fakten außerhalb der Vorgaben.\n") if kind == "long" else "")
        + hint
        + (f"Diese Titel gab es schon, wähle einen anderen Blickwinkel: {avoid}\n" if avoid else "")
    )
    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=config.ANTHROPIC_MODEL, max_tokens=16000, system=system,
        tools=[TOOL if kind == "short" else TOOL_LONG], tool_choice={"type": "tool", "name": "skript"},
        messages=[{"role": "user", "content": user}],
    )
    data = next(b for b in resp.content if b.type == "tool_use").input
    if not data.get("szenen"):
        raise ValueError("Skript ohne Szenen")
    data["titel"] = (data.get("titel") or data.get("title") or topic["titel"]).strip()
    data["beschreibung"] = (data.get("beschreibung") or data.get("description") or topic["titel"]).strip()
    data["tags"] = data.get("tags") or []
    return data


# ---------------------------------------------------------------- Sprache
def speakable(t):
    t = t.replace("pflege-mit-plan.com", "pflege-mit-plan punkt com")
    t = re.sub(r"(\d)\s?€", r"\1 Euro", t).replace("€", "Euro").replace("§§", "Paragrafen").replace("§", "Paragraf")
    t = t.replace("z. B.", "zum Beispiel").replace("z.B.", "zum Beispiel").replace("ca.", "circa")
    return t


def spoken_words(data):
    return sum(len(str(sz.get("sprecher", "")).split()) for sz in data["szenen"])


def video_seconds(p):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                         check=True, capture_output=True, text=True).stdout.strip()
    return float(out or 0)


def wav_seconds(p):
    with wave.open(str(p)) as w:
        return w.getnframes() / w.getframerate()


# ---------------------------------------------------------------- Bilder
def font(path, size):
    return ImageFont.truetype(path, size)


def heart(d, cx, cy, s, fill):
    pts = []
    for i in range(240):
        t = 2 * math.pi * i / 240
        x = 16 * math.sin(t) ** 3
        y = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
        pts.append((cx + x * s / 34, cy - y * s / 34))
    d.polygon(pts, fill=fill)


def wrap(d, text, f, maxw):
    words, lines, cur = text.split(), [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if d.textlength(test, font=f) <= maxw:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def scene_image(sz, idx, total, reihe, size, path):
    W, H = size
    vertical = H > W
    img = Image.new("RGB", size, SAND)
    d = ImageDraw.Draw(img)
    d.ellipse([-W * 0.25, H * 0.7, W * 0.45, H * 1.3], fill=MINT)
    d.ellipse([W * 0.7, -H * 0.25, W * 1.25, H * 0.3], fill=MINT)
    u = W / 1080 if vertical else H / 1080
    # Kopf
    heart(d, 120 * u, 115 * u, 64 * u, GREEN)
    d.text((165 * u, 92 * u), "Pflege mit Plan", font=font(FONT_B, int(38 * u)), fill=GREEN)
    label = "Für Angehörige" if reihe == "angehoerige" else "Pflege lernen"
    fl = font(FONT_B, int(28 * u)); lw = d.textlength(label, font=fl)
    d.rounded_rectangle([W - lw - 150 * u, 85 * u, W - 100 * u, 145 * u], radius=30 * u, fill=GREEN if reihe == "azubi" else ORANGE)
    d.text((W - lw - 125 * u, 98 * u), label, font=fl, fill=(255, 255, 255))
    # grosse Schritt-Marke (Motion-Graphics-Anmutung)
    br = (150 if vertical else 120) * u
    bx, byy = (W - 220 * u - 2 * br, H * 0.30) if not vertical else (W / 2 - br, H * 0.52)
    d.ellipse([bx, byy, bx + 2 * br, byy + 2 * br], fill=MINT, outline=GREEN, width=int(8 * u))
    fn = font(FONT_B, int(br * 1.0)); num = str(idx + 1)
    d.text((bx + br - d.textlength(num, font=fn) / 2, byy + br - fn.size * 0.62), num, font=fn, fill=GREEN)
    # Tafel
    maxw = W - (200 if vertical else 560) * u
    ft = font(FONT_B, int((96 if vertical else 84) * u))
    lines = wrap(d, sz["tafel"], ft, maxw)
    while len(lines) > 4:
        ft = font(FONT_B, int(ft.size * 0.88)); lines = wrap(d, sz["tafel"], ft, maxw)
    lh = ft.size * 1.15
    top = H * (0.20 if vertical else 0.26)
    for i, ln in enumerate(lines):
        d.text((100 * u, top + i * lh), ln, font=ft, fill=GREEN)
    y = top + len(lines) * lh + 20 * u
    d.rectangle([100 * u, y, 220 * u, y + 10 * u], fill=ORANGE)
    fu = font(FONT_R, int((50 if vertical else 44) * u))
    for i, ln in enumerate(wrap(d, sz["unter"], fu, maxw)[:3]):
        d.text((100 * u, y + 40 * u + i * fu.size * 1.25), ln, font=fu, fill=DARK)
    # Untertitel-Kasten
    fs = font(FONT_B, int((40 if vertical else 34) * u))
    sub = wrap(d, sz["sprecher"], fs, W - 260 * u)[:5]
    bh = len(sub) * fs.size * 1.3 + 50 * u
    by = H - bh - (200 if vertical else 90) * u
    d.rounded_rectangle([90 * u, by, W - 90 * u, by + bh], radius=28 * u, fill=GREEN)
    for i, ln in enumerate(sub):
        d.text((130 * u, by + 25 * u + i * fs.size * 1.3), ln, font=fs, fill=(255, 255, 255))
    # Fortschritt
    seg = (W - 200 * u) / total
    for i in range(total):
        d.rounded_rectangle([100 * u + i * seg, H - 70 * u, 100 * u + (i + 1) * seg - 12 * u, H - 58 * u], radius=6 * u,
                            fill=ORANGE if i <= idx else (210, 210, 200))
    img.save(path, quality=92)


# ---------------------------------------------------------------- Video
def run(cmd):
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def render(data, topic, kind, workdir):
    workdir.mkdir(parents=True, exist_ok=True)
    size = (1080, 1920) if kind == "short" else (1920, 1080)
    scenes = data["szenen"]
    clips, audios = [], []
    for i, sz in enumerate(scenes):
        wav = workdir / f"s{i:02d}.wav"
        piper_engine.speak_to_wav(speakable(sz["sprecher"]), str(wav), length_scale=1.05)
        dur = wav_seconds(wav) + 0.6
        png = workdir / f"s{i:02d}.jpg"
        scene_image(sz, i, len(scenes), topic["reihe"], size, png)
        clip = workdir / f"s{i:02d}.mp4"
        frames = int(dur * 30)
        z = "min(zoom+0.0006,1.06)"
        run(["ffmpeg", "-y", "-loop", "1", "-i", str(png), "-i", str(wav),
             "-filter_complex",
             f"[0:v]scale={size[0]*2}:{size[1]*2},zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={size[0]}x{size[1]}:fps=30,"
             f"fade=t=in:st=0:d=0.35,fade=t=out:st={dur-0.35:.2f}:d=0.35[v];[1:a]apad=pad_dur=0.6[a]",
             "-map", "[v]", "-map", "[a]", "-t", f"{dur:.2f}", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-ar", "44100", str(clip)])
        clips.append(clip)
    lst = workdir / "list.txt"
    lst.write_text("".join(f"file '{c.name}'\n" for c in clips), encoding="utf-8")
    joined = workdir / "joined.mp4"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(joined)])
    music = build_music_loop(workdir / "music.wav", seed=topic["slug"] + kind, minutes=2.0 if kind == "short" else 14.0)
    out = workdir / "video.mp4"
    run(["ffmpeg", "-y", "-i", str(joined), "-stream_loop", "-1", "-i", str(music),
         "-filter_complex", "[1:a]volume=0.10[m];[0:a][m]amix=inputs=2:duration=first:dropout_transition=0[a]",
         "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", str(out)])
    thumb = workdir / "s00.jpg"
    return out, thumb


# ---------------------------------------------------------------- Metadaten / Upload
def description(data, topic, kind):
    link = topic["quelle"] if topic["reihe"] == "angehoerige" else SITE + "/pflege-lernen"
    amazon = f"https://www.amazon.de/s?k={requests.utils.quote(topic['amazon'])}&tag={config.AMAZON_TAG}"
    lines = [data["beschreibung"].strip(), "",
             f"👉 Alles Wichtige zum Thema: {link}",
             f"📚 Passende Bücher auf Amazon: {amazon}",
             "(Werbung: Als Amazon-Partner verdiene ich an qualifizierten Verkäufen.)", "",
             DISCLAIMER, "",
             "#Pflege #" + ("pflegendeAngehörige" if topic["reihe"] == "angehoerige" else "Pflegeausbildung") + " #PflegeMitPlan"]
    if kind == "short":
        lines.append("#Shorts")
    return "\n".join(lines)


def publish_at(day, hhmm):
    h, m = map(int, hhmm.split(":"))
    local = dt.datetime.combine(day, dt.time(h, m), TZ)
    if local < dt.datetime.now(TZ) + dt.timedelta(minutes=20):
        return None
    return local.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def upload(path, thumb, data, topic, kind, when):
    from upload.upload_video import upload as yt_upload
    title = data["titel"].strip()[:95]
    if kind == "short" and "#shorts" not in title.lower():
        title = (title[:88] + " #Shorts")
    tags = list(dict.fromkeys((data.get("tags") or []) + ["Pflege", "Pflege mit Plan",
                ("pflegende Angehörige" if topic["reihe"] == "angehoerige" else "Pflegeausbildung")]))[:25]
    if REVIEW:
        return yt_upload(str(path), title, description(data, topic, kind), tags, privacy_status="private",
                         thumbnail_path=str(thumb) if kind == "long" else None)
    return yt_upload(str(path), title, description(data, topic, kind), tags, publish_at=when,
                     privacy_status=None if when else "public", thumbnail_path=str(thumb) if kind == "long" else None)


# ---------------------------------------------------------------- Tageslauf
GAMING_UPLOAD = "https://kfiiaqjzzwwacthzrcut.supabase.co/functions/v1/gaming-upload"
TIKTOK_TAGS = {
    "angehoerige": "#pflege #pflegendeangehörige #pflegegrad #pflegetipps #angehörige",
    "azubi": "#pflegeausbildung #pflegeazubi #pflegefachfrau #pflegefachmann #pflegeassistenz",
}


def tiktok_queue(path, data, topic, reihe):
    """Short zusätzlich als TikTok-Entwurf einreihen (gaming-upload pumpt alle 30 Min ins Postfach)."""
    tok = os.environ.get("PFLEGE_UPLOAD_TOKEN", "").strip()
    if not tok:
        print("  TikTok: kein PFLEGE_UPLOAD_TOKEN, übersprungen")
        return None
    h = {"x-gaming-token": tok, "Content-Type": "application/json"}
    name = re.sub(r"[^\w\-]", "_", f"pflege-{topic['slug']}-{dt.datetime.now(TZ):%H%M%S}")[:70] + ".mp4"
    r = requests.post(GAMING_UPLOAD, json={"action": "upload_url", "name": name}, headers=h, timeout=60).json()
    if "signed_url" not in r:
        raise RuntimeError(f"TikTok upload_url: {r}")
    with open(path, "rb") as f:
        up = requests.put(r["signed_url"], data=f, headers={"Content-Type": "video/mp4", "x-upsert": "true"}, timeout=300)
    if up.status_code >= 300:
        raise RuntimeError(f"TikTok Upload {up.status_code}: {up.text[:200]}")
    caption = f"{data['titel']} 💚 Mehr auf pflege-mit-plan.com\n{TIKTOK_TAGS.get(reihe, TIKTOK_TAGS['angehoerige'])}"
    reg = requests.post(GAMING_UPLOAD, json={"action": "register", "path": r["path"], "title": data["titel"],
                                              "caption": caption, "seconds": round(video_seconds(path), 1)},
                        headers=h, timeout=60).json()
    print("  TikTok eingereiht:", reg)
    return reg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--only", choices=["short", "long"], help="nur diese Art erzeugen (Nachholen)")
    ap.add_argument("--tiktok-only", type=int, default=0, help="N Shorts nur für TikTok bauen (ohne YouTube)")
    a = ap.parse_args()
    today = dt.datetime.now(TZ).date()
    rng = random.Random(today.isoformat())
    st = load_state()
    jobs = [("short", "angehoerige", SHORT_TIMES[0]), ("short", "azubi", SHORT_TIMES[1])]
    if today.weekday() in LONG_DAYS:
        reihe = "angehoerige" if (today.isocalendar().week + today.weekday()) % 2 == 0 else "azubi"
        jobs.append(("long", reihe, LONG_TIME))
    if a.only == "long" and not any(j[0] == "long" for j in jobs):
        jobs.append(("long", "angehoerige", LONG_TIME))
    if a.only:
        jobs = [j for j in jobs if j[0] == a.only]
    if a.test:
        jobs = jobs[:1]
    if a.tiktok_only:
        jobs = [("short", "angehoerige" if k % 2 == 0 else "azubi", None) for k in range(a.tiktok_only)]
    failures = 0
    for i, (kind, reihe, hhmm) in enumerate(jobs):
        topic = pick(reihe, st, rng)
        avoid = [t for t in st["titles"] if t.startswith(topic["slug"] + "|")][-6:]
        print(f"[{kind}] {topic['titel']}")
        try:
            titles_avoid = [x.split("|", 1)[1] for x in avoid]
            data = script(topic, kind, titles_avoid) if kind == "short" else long_script(topic, titles_avoid)
            if kind == "long":
                for _ in range(2):
                    n = spoken_words(data)
                    if n >= LONG_MIN_WORDS:
                        break
                    print(f"  Skript zu kurz ({n} Wörter), neuer Versuch")
                    data = long_script(topic, titles_avoid, hint=f"WICHTIG: Der letzte Entwurf hatte nur {n} Wörter. Schreibe ausführlicher: jede Szene mindestens 60 gesprochene Wörter.\n")
            path, thumb = render(data, topic, kind, OUT / today.isoformat() / f"{i}-{kind}-{topic['slug']}")
            if kind == "long":
                secs = video_seconds(path)
                print(f"  Länge: {secs / 60:.1f} Min")
                if secs < LONG_MIN_SECONDS:
                    raise RuntimeError(f"Longform nur {secs / 60:.1f} Min - Regel: mindestens 10 Min, wird nicht hochgeladen")
            print("  gerendert:", path, f"{path.stat().st_size // 1024} KB")
            if a.test:
                continue
            if kind == "short":
                try:
                    tiktok_queue(path, data, topic, reihe)
                except Exception as e:  # noqa: BLE001
                    print("  TikTok FEHLER:", str(e)[:300])
            if not a.tiktok_only:
                vid = upload(path, thumb, data, topic, kind, publish_at(today, hhmm))
                print("  hochgeladen:", vid, "(privat zur Sichtung)" if REVIEW else hhmm)
            st["used"][topic["slug"]] = st["used"].get(topic["slug"], 0) + 1
            st["titles"].append(f"{topic['slug']}|{data['titel']}")
            st["titles"] = st["titles"][-400:]
            STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            failures += 1
            print("  FEHLER:", str(e)[:400])
    if failures == len(jobs):
        sys.exit(1)


if __name__ == "__main__":
    main()
