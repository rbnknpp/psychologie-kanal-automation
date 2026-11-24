"""Shorts aus einer fertigen Was-wäre-wenn-Folge (Hochformat 1080x1920, 25-55 s).

Abschnitte: ep["shorts"] = [{"von": i, "bis": j, "titel": "Hook"}] (Szenen-Indizes, 0-basiert) oder automatisch
ab Szenen mit großer Texteinblendung. Bild + Ton kommen aus der Folge (timeline.json, voice.mp3, bilder/).
Upload geplant: 2 pro Tag, 12 und 19 Uhr.
"""
import datetime as dt
import json
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from pflege.tiktok_news import chunks, font, wrap

W, H, FPS = 1080, 1920, 30
YELLOW, WHITE, BLACK = (255, 214, 70), (255, 255, 255), (0, 0, 0)
TIMES = ["12:00", "19:00"]


def auto_segments(tl, n=6):
    sc = tl["scenes"]
    segs, i = [], 1
    while i < len(sc) and len(segs) < n:
        if sc[i].get("titel"):
            j = i
            while j + 1 < len(sc) and sc[j + 1]["t1"] - sc[i]["t0"] < 50:
                j += 1
            if sc[j]["t1"] - sc[i]["t0"] >= 22:
                segs.append({"von": i, "bis": j, "titel": ""})
                i = j + 1
                continue
        i += 1
    return segs


def cover(im, w, h):
    s = max(w / im.width, h / im.height)
    r = im.resize((int(im.width * s) + 1, int(im.height * s) + 1), Image.LANCZOS)
    x, y = (r.width - w) // 2, (r.height - h) // 2
    return r.crop((x, y, x + w, y + h))


def header(titel, frage=""):
    """Oben klein die Frage der Folge (z. B. „Was wäre, wenn du 1.000 Jahre alt werden könntest?“), darunter groß der Hook.
    Robin 24.11.2026: früher stand fest „WAS WÄRE, WENN …“ vor dem Hook -> ergab Unsinn („… Dieser Hai ist 392 Jahre alt?!“)."""
    lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    y = 230
    if frage and frage.strip().lower() != titel.strip().lower():
        fs = font(44, "ExtraBold")
        for ln in wrap(d, frage.upper(), fs, 960)[:2]:
            d.text((W / 2, y), ln, font=fs, fill=WHITE, anchor="mt", stroke_width=5, stroke_fill=BLACK)
            y += 54
        y += 26
    f = font(82, "ExtraBold")
    lines = wrap(d, titel.upper(), f, 960)[:3]
    for ln in lines:
        d.text((W / 2, y), ln, font=f, fill=YELLOW, anchor="mt", stroke_width=9, stroke_fill=BLACK)
        y += 96
    return lay


def caption(text):
    lay = Image.new("RGBA", (W, 160), (0, 0, 0, 0))
    ImageDraw.Draw(lay).text((W / 2, 80), text.upper(), font=font(66, "ExtraBold"), fill=WHITE, anchor="mm", stroke_width=8, stroke_fill=BLACK)
    return lay


def endcard():
    lay = Image.new("RGBA", (W, 200), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    d.rounded_rectangle((140, 30, W - 140, 170), radius=40, fill=(220, 30, 40))
    d.text((W / 2, 100), "GANZE FOLGE AUF DEM KANAL ▶", font=font(48, "ExtraBold"), fill=WHITE, anchor="mm")
    return lay


def render_short(wd, tl, seg, titel, out, frage=""):
    sc = tl["scenes"][seg["von"]:seg["bis"] + 1]
    t0, t1 = sc[0]["t0"], sc[-1]["t1"]
    dur = t1 - t0 + 0.4
    imgs = {}
    for s in sc:
        if s["bild"] not in imgs:
            im = Image.open(wd / s["bild"]).convert("RGB")
            bg = cover(im, W, H).filter(ImageFilter.GaussianBlur(28)).point(lambda v: int(v * 0.55))
            imgs[s["bild"]] = (bg.convert("RGBA"), im.resize((int(W * 1.1), int(W * 1.1 * 9 / 16)), Image.LANCZOS))
    head = header(titel, frage)
    caps = [(a - t0, z - t0, caption(t)) for s in sc for a, z, t in chunks([tuple(w) for w in s["words"]], maxw=3, maxc=22)]
    end = endcard()
    vid = out.with_suffix(".silent.mp4")
    ff = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                           "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", str(vid)],
                          stdin=subprocess.PIPE)
    si = 0
    for fi in range(int(dur * FPS)):
        t = fi / FPS
        while si + 1 < len(sc) and t >= sc[si + 1]["t0"] - t0:
            si += 1
        bg, fg = imgs[sc[si]["bild"]]
        fr = bg.copy()
        z = 1.0 + 0.04 * (t / dur)
        fw, fh = int(fg.width * z), int(fg.height * z)
        f2 = fg.resize((fw, fh), Image.BILINEAR)
        fr.paste(f2, ((W - fw) // 2, 960 - fh // 2 + 60))
        fr.alpha_composite(head)
        for a, b, c in caps:
            if a - 0.05 <= t < b + 0.25:
                fr.alpha_composite(c, (0, 1330))
                break
        if t > dur - 2.6:
            fr.alpha_composite(end, (0, 1560))
        ff.stdin.write(fr.convert("RGB").tobytes())
    ff.stdin.close()
    ff.wait()
    music = wd / "music.wav"
    inputs = ["-i", str(vid), "-ss", f"{max(0, t0 - 0.1):.2f}", "-t", f"{dur:.2f}", "-i", str(wd / "voice.mp3")]
    af = "[1:a]aformat=sample_rates=44100:channel_layouts=stereo,afade=t=out:st=" + f"{dur - 0.4:.2f}" + ":d=0.4[a]"
    if music.exists():
        inputs += ["-ss", f"{t0:.2f}", "-i", str(music)]
        af = ("[1:a]aformat=sample_rates=44100:channel_layouts=stereo[v];[2:a]aformat=sample_rates=44100:channel_layouts=stereo,volume=0.14[m];"
              f"[v][m]amix=inputs=2:duration=first:normalize=0,afade=t=out:st={dur - 0.4:.2f}:d=0.4[a]")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", af, "-map", "0:v", "-map", "[a]", "-t", f"{dur:.2f}",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)], check=True)
    vid.unlink(missing_ok=True)
    return out, dur


def slots(start, days):
    """2 Termine pro Tag; Termine, die schon (fast) vorbei sind, werden übersprungen."""
    from zoneinfo import ZoneInfo
    now = dt.datetime.now(ZoneInfo("Europe/Berlin")) + dt.timedelta(minutes=30)
    d0 = dt.date.fromisoformat(start)
    out = []
    for k in range(days):
        for hh in TIMES:
            h, m = map(int, hh.split(":"))
            when = dt.datetime.combine(d0 + dt.timedelta(days=k), dt.time(h, m), ZoneInfo("Europe/Berlin"))
            if when > now:
                out.append((str(d0 + dt.timedelta(days=k)), hh))
    return out


def make_shorts(ep, wd, start, days, upload=False, main_video=None):
    tl = json.loads((wd / "timeline.json").read_text(encoding="utf-8"))
    segs = ep.get("shorts") or auto_segments(tl, n=len(TIMES) * days)
    plan = slots(start, days)
    sd = wd / "shorts"
    sd.mkdir(exist_ok=True)
    done = []
    for k, (seg, (day, hh)) in enumerate(zip(segs, plan)):
        frage = ep["titel"].strip()
        titel = seg.get("titel") or ep.get("short_titel") or frage
        path, dur = render_short(wd, tl, seg, titel, sd / f"short_{k + 1}.mp4", frage)
        print(f"  Short {k + 1}: {titel} ({dur:.0f} s) -> {day} {hh}")
        if upload:
            from upload.upload_video import upload as yt_upload
            from waswaerewenn.make import publish_at
            link = f"\n\n▶ Ganze Folge: https://youtu.be/{main_video}" if main_video else "\n\n▶ Die ganze Folge findest du auf dem Kanal."
            desc = (f"{titel}\n\n{frage}{link}\n\nNeue Folgen jeden Mo, Mi und Fr um 17 Uhr.\n\n"
                    "#shorts #waswärewenn #wissen #gedankenexperiment")
            title = titel[:90] + " #shorts"
            vid = yt_upload(str(path), title, desc, ep.get("tags", []) + ["shorts"], publish_at=publish_at(day, hh))
            print("    hochgeladen:", vid)
        done.append(str(path))
    return done
