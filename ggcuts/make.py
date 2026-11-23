"""gg.cuts.tv – taegliche Gaming-Clips aus SkiiMaskTimmys Twitch-VODs (Content-Rewards-Kampagne).

Ablauf (GitHub-Workflow ggcuts-daily):
  1. neuestes VOD von twitch.tv/skiimasktimmy holen, das noch nicht ausgeschoepft ist (state.json)
  2. Tonspur laden, laute Momente (relativ zum Umfeld) als Kandidaten suchen
  3. Kandidaten transkribieren (faster-whisper), Claude waehlt die besten N Clips + Hook + Caption
  4. Clip in 1080p laden, 9:16 schneiden: Facecam gross + Gameplay (bzw. Kamera bildfuellend bei Just Chatting),
     Pflicht-Overlay twitch.tv/skiimasktimmy, Wort-fuer-Wort-Untertitel, Originalton (keine Musik)
  5. ueber gaming-upload (Kanal ggcuts) einreihen -> Cron schickt sie als Entwurf ins Postfach von @gg.cuts.tv

Kampagnenregeln (ClippedIn): nur Timmys Twitch-Streams, Facecam sichtbar, Overlay + "twitch.tv/skiimasktimmy"
in der Caption, Stream-Ton hoerbar, >= 7 s, 9:16, englisch.
"""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:  # Kosten-Protokoll (Supabase api_usage)
    import usage_log
    usage_log.install()
except Exception:  # noqa: BLE001
    pass

HERE = Path(__file__).parent
WORK = HERE / "work"
STATE = HERE / "state.json"
CAMPAIGNS = json.loads((HERE / "campaigns.json").read_text(encoding="utf-8"))
UPLOAD = "https://kfiiaqjzzwwacthzrcut.supabase.co/functions/v1/gaming-upload"
MAX_PER_VOD = 9  # bei langen VODs (>6 h) gilt das je 4-h-Abschnitt nicht – dort begrenzt scan_pos


def sh(cmd, **kw):
    print("$", " ".join(str(c) for c in cmd)[:200], flush=True)
    return subprocess.run(cmd, check=True, **kw)


def load_state():
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {"vods": {}}


def save_state(st):
    STATE.write_text(json.dumps(st, indent=1, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------- VOD + Kandidaten
def vod_url(camp, vod):
    return f"https://www.twitch.tv/videos/{vod}"


def vods(camp):
    import time
    for attempt in range(4):  # Twitch liefert vom Runner gelegentlich eine leere Liste
        out = subprocess.run(["yt-dlp", "--flat-playlist", "-J",
                              f"https://www.twitch.tv/{camp['channel']}/videos?filter=archives&sort=time"],
                             capture_output=True, text=True, encoding="utf-8", check=True).stdout
        entries = json.loads(out).get("entries") or []
        if entries:
            return [(e["id"].lstrip("v"), e.get("title", ""), e.get("duration") or 0) for e in entries]
        print(f"  VOD-Liste leer, neuer Versuch {attempt + 1}", flush=True)
        time.sleep(20)
    return []


def peaks(audio, top=14, win=2.0, skip=(), guard=90):
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(audio), "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768
    n = int(8000 * win)
    rms = np.sqrt(np.add.reduceat(a * a, np.arange(0, len(a), n)) / n)
    base = np.convolve(rms, np.ones(150) / 150, mode="same") + 1e-4
    score = rms / base
    picked = []
    for i in np.argsort(score)[::-1]:
        t = float(i * win)
        if t < 300 or t > len(rms) * win - 120:
            continue
        if any(abs(t - p) < guard for p in picked) or any(s - 30 <= t <= e + 30 for s, e in skip):
            continue
        picked.append(t)
        if len(picked) >= top:
            break
    return sorted(picked)


_model = None


def whisper():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        _model = WhisperModel(os.environ.get("WHISPER_MODEL", "small"), device="cpu", compute_type="int8")
    return _model


def transcribe_window(audio, t, pre=30, post=25):
    wav = WORK / "seg.wav"
    s = max(0, t - pre)
    sh(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(s), "-t", str(pre + post), "-i", str(audio),
        "-ac", "1", "-ar", "16000", str(wav)])
    segs, _ = whisper().transcribe(str(wav), language="en", vad_filter=True)
    return [(round(x.start + s, 1), round(x.end + s, 1), x.text.strip()) for x in segs]


def choose(cands, n, title, camp):
    import anthropic
    lines = []
    for i, (t, segs) in enumerate(cands):
        lines.append(f"### Kandidat {i} (Lautstaerke-Spitze bei {t:.0f}s)")
        lines += [f"[{a:.1f}-{b:.1f}] {txt}" for a, b, txt in segs] or ["(kaum Sprache)"]
    prompt = f"""You pick TikTok clips from a Twitch VOD of streamer {camp['name']} (stream title: {title!r}).
Campaign formats that perform: {camp.get('formats', 'clutch plays, funny moments & fails, genuine reactions, hot takes, chat banter')}.
Avoid: sponsor reads, ads, gambling, bathroom breaks, menus/loading, quiet talk, anything that needs long context
or shows the streamer in a negative light.

Below are transcribed windows around loudness peaks (timestamps in seconds of the VOD).
Choose the {n} best, non-overlapping clips (ranked best first; some may be dropped later if his facecam is not visible). Each clip: 15-45 seconds, must start right before the payoff/hook
(no slow intro) and end shortly after the reaction. Use the timestamps.

Return ONLY JSON: {{"clips":[{{"start":float,"end":float,"hook":"max 6 words, punchy, English, no emoji",
"caption":"short English caption with 1 emoji, max 90 chars, no hashtags","score":1-10}}]}}
Skip weak windows - if fewer than {n} are good, return fewer.

{chr(10).join(lines)}"""
    msg = anthropic.Anthropic().messages.create(model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5"),
                                                max_tokens=8000, messages=[{"role": "user", "content": prompt}])
    txt = "".join(getattr(b, "text", "") for b in msg.content if getattr(b, "type", "") == "text")
    print("Claude:", txt[:600], flush=True)
    data = json.loads(txt[txt.find("{"):txt.rfind("}") + 1])
    clips = [c for c in data.get("clips", []) if 7 <= float(c["end"]) - float(c["start"]) <= 60]
    return sorted(clips, key=lambda c: -float(c.get("score", 0)))[:n]


# ---------------------------------------------------------------- Rendern
# Schutzzonen fuer TikTok + Instagram Reels (1080x1920): oben ~220 px Kopfzeile, unten ~480 px Caption/Buttons,
# rechts ~130 px Like-/Kommentar-Leiste. Text bleibt in x 120..960 (max. 820 px breit) und y 240..1400.
TEXT_MAX_W = 820
SUB_FS, HOOK_FS = 96, 76
_FONTS = {}


def text_w(txt, size):
    from PIL import ImageFont
    f = _FONTS.get(size) or _FONTS.setdefault(size, ImageFont.truetype(str(HERE / "fonts" / "Anton-Regular.ttf"), size))
    asc, desc = f.getmetrics()
    # libass skaliert so, dass Ober- + Unterlaenge = Schriftgroesse -> Glyphen kleiner als bei PIL
    return f.getlength(txt) * size / (asc + desc) + len(txt) * 1 + 14   # + Spacing 1 je Zeichen + Umrandung


def fit_fs(txt, size, max_w=TEXT_MAX_W, min_size=54):
    while size > min_size and text_w(txt, size) > max_w:
        size -= 2
    return size


def wrap_hook(hook):
    """Hook in max. 2 Zeilen umbrechen; Schrift notfalls verkleinern, damit nichts aus dem Bild ragt."""
    words, size = hook.upper().split(), HOOK_FS
    while True:
        lines, cur = [], ""
        for w in words:
            t = f"{cur} {w}".strip()
            if cur and text_w(t, size) > TEXT_MAX_W:
                lines.append(cur)
                cur = w
            else:
                cur = t
        lines.append(cur)
        if (len(lines) <= 2 and all(text_w(l, size) <= TEXT_MAX_W for l in lines)) or size <= 50:
            return "\\N".join(lines), size
        size -= 4


def ass_time(t):
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def subtitles(src, ass, hook, sub_y, hook_y):
    segs, _ = whisper().transcribe(str(src), language="en", word_timestamps=True, vad_filter=True)
    words = [w for s in segs for w in s.words if w.word.strip()]
    head = f"""[Script Info]
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: W,Anton,96,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,0,0,0,0,100,100,1,0,1,6,2,5,40,40,0,1
Style: H,Anton,76,&H0000E6FF,&H0000E6FF,&H00000000,&H96000000,0,0,0,0,100,100,1,0,3,5,0,5,60,60,0,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = []
    if hook:
        htxt, hfs = wrap_hook(hook)
        ev.append(f"Dialogue: 1,{ass_time(0)},{ass_time(3.2)},H,,0,0,0,,{{\\pos(540,{hook_y})\\fs{hfs}}}{htxt}")
    for i in range(0, len(words), 3):
        grp = words[i:i + 3]
        end = grp[-1].end + 0.15
        if i + 3 < len(words):
            end = min(end, words[i + 3].start)
        plain = " ".join(g.word.strip().upper() for g in grp)
        fs = fit_fs(plain, SUB_FS)                 # lange Woerter -> kleinere Schrift statt Ueberlauf
        for k, w in enumerate(grp):
            we = grp[k + 1].start if k + 1 < len(grp) else end
            txt = " ".join(("{\\c&H0000E6FF&}" + g.word.strip().upper() + "{\\c&H00FFFFFF&}") if g is w
                           else g.word.strip().upper() for g in grp)
            ev.append(f"Dialogue: 0,{ass_time(w.start)},{ass_time(max(we, w.start + 0.05))},W,,0,0,0,,"
                      f"{{\\pos(540,{sub_y})\\fs{fs}}}{txt}")
    ass.write_text(head + "\n".join(ev), encoding="utf-8")
    return " ".join(w.word.strip() for w in words)


def frame_at(video, t, name):
    jpg = WORK / name
    sh(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(t), "-i", str(video), "-frames:v", "1", "-vf", "scale=1920:1080", str(jpg)])
    from PIL import Image
    return np.asarray(Image.open(jpg).convert("RGB"))


def faces(img, min_size=60, neighbors=5):
    import cv2
    casc = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    gray = cv2.equalizeHist(cv2.cvtColor(np.ascontiguousarray(img), cv2.COLOR_RGB2GRAY))
    return casc.detectMultiScale(gray, scaleFactor=1.08, minNeighbors=neighbors, minSize=(min_size, min_size))


def border_match(im, box, color):
    """Rahmen der Facecam-Box vorhanden? color = "teal" (animiert leuchtend) oder [r, g, b] mit Toleranz."""
    x, y, w, h = box
    band = im[max(0, y - 14):y + 8].astype(int)
    def ok(px):
        if color == "teal":
            return px[1] > 150 and px[2] > 150 and px[1] - px[0] > 35
        return sum(abs(int(px[k]) - color[k]) for k in range(3)) < 90
    hits = sum(1 for xx in range(x + 40, x + w - 20, 20) if any(ok(px) for px in band[:, xx]))
    return hits >= 6


def face_track(video, dur, crop_w=608):
    """Gesicht verfolgen (4 Bilder/s): liefert [(t, x_links)] fuer einen 608 px breiten Ausschnitt (1920x1080),
    weich gefuehrt wie eine Kamera. None, wenn in < 30 % der Bilder ein Gesicht erkannt wird."""
    from PIL import Image
    d = WORK / "trk"
    if d.exists():
        for f in d.iterdir():
            f.unlink()
    d.mkdir(exist_ok=True)
    sh(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-vf", "fps=4,scale=960:540", str(d / "f%04d.jpg")])
    files = sorted(d.iterdir())
    raw, prev = [], None
    for f in files:
        im = np.asarray(Image.open(f).convert("RGB"))
        fs = [tuple(int(v) for v in x) for x in faces(im, min_size=40, neighbors=6)]
        cx = None
        if fs:
            big = max(fs, key=lambda q: q[2] * q[3])
            cands = [q for q in fs if q[2] * q[3] >= 0.5 * big[2] * big[3]]
            pick = min(cands, key=lambda q: abs((q[0] + q[2] / 2) * 2 - prev)) if prev is not None else big
            cx = (pick[0] + pick[2] / 2) * 2
            prev = cx
        raw.append(cx)
    hits = sum(c is not None for c in raw)
    print(f"  Gesicht in {hits}/{len(raw)} Bildern", flush=True)
    if not raw or hits < 0.3 * len(raw):
        return None
    first = next(c for c in raw if c is not None)
    filled, last = [], first
    for c in raw:
        last = c if c is not None else last
        filled.append(last)
    med = np.array([float(np.median(filled[max(0, i - 2):i + 3])) for i in range(len(filled))])
    # auf 30 Bilder/s hochrechnen und weich glaetten -> gleitende Kamerafahrt statt Spruengen
    fps = 30
    n = max(2, int(round(len(med) / 4 * fps)))
    tt = np.arange(n) / fps
    target = np.interp(tt, np.arange(len(med)) / 4, med)
    sig = int(0.35 * fps)
    k = np.exp(-0.5 * (np.arange(-3 * sig, 3 * sig + 1) / sig) ** 2)
    k /= k.sum()
    pad = np.pad(target, (3 * sig, 3 * sig), mode="edge")
    smooth = np.convolve(pad, k, mode="valid")
    # Kamera mit Traegheit (kritisch gedaempfte Feder) und Hoechstgeschwindigkeit
    cam, vel, out = smooth[0], 0.0, []
    vmax = 900 / fps                       # max. 900 px/s
    for i, tgt in enumerate(smooth):
        acc = (tgt - cam) * 0.02 - vel * 0.28
        vel = float(np.clip(vel + acc * 1.0, -vmax, vmax))
        cam += vel
        x = int(round(min(max(cam - crop_w / 2, 0), 1920 - crop_w)))
        out.append((tt[i], x))
    return out


def layout(video, dur, camp):
    """('cam', box) = Gameplay mit Facecam-Box (Position je Streamer in campaigns.json: cam_box + cam_border),
    ('full', None) = Kamera bildfuellend (Just Chatting), (None, None) = keine Facecam sichtbar -> Clip verwerfen."""
    if camp.get("layout") == "full":  # IRL-/Kamera-Streams ohne Facecam-Box: Ausschnitt folgt dem Gesicht
        track = face_track(video, dur)
        return ("track", track) if track else (None, None)
    votes = []
    box = tuple(camp["cam_box"]) if camp.get("cam_box") else None
    for i in range(5):
        t = max(0.5, min(dur - 1, 0.5 + i * (dur - 1.5) / 4))
        im = frame_at(video, round(t, 1), f"probe{i}.jpg")
        if box and border_match(im, box, camp.get("cam_border", "teal")):
            votes.append("cam")
        elif len(faces(im[:, 560:1360], min_size=120, neighbors=5)) > 0:
            votes.append("full")
        else:
            votes.append(None)
        print(f"  Probe {t:.0f}s -> {votes[-1]}", flush=True)
    if votes.count("cam") >= 3:
        return "cam", box
    if votes.count("full") >= 3:
        return "full", None
    return None, None


def render(camp, vod, start, end, name, hook):
    raw = WORK / f"{name}_raw.mp4"
    out = WORK / f"{name}.mp4"
    sh(["yt-dlp", "--no-progress", "-f", "b[vcodec!=none][height<=1080]/b[vcodec!=none]", "-S", "res:1080,fps",
        "--download-sections", f"*{start:.1f}-{end:.1f}", "--force-keyframes-at-cuts", "-o", str(raw),
        vod_url(camp, vod)])
    lay, box = layout(raw, end - start, camp)
    if lay is None:
        print("  keine Facecam sichtbar - Clip verworfen", flush=True)
        raw.unlink(missing_ok=True)
        return None, "", None
    cam_layout = lay == "cam"
    ass = WORK / f"{name}.ass"
    # Facecam-Layout: Kamera oben (0..760), Hook im Gameplay darunter. Kamera bildfuellend: Gesicht sitzt
    # ca. bei y 450..900 -> Hook unterhalb des Gesichts. Untertitel immer ueber der TikTok-Caption-Zone (< 1400).
    sub_y, hook_y = (1270, 900) if cam_layout else (1320, 1170)
    text = subtitles(raw, ass, hook, sub_y, hook_y)
    ov_file = HERE / camp["overlay"] if camp.get("overlay") else None
    if cam_layout:
        x, y, w, h = box
        cam_h = min(760, int(1080 * h / w) // 2 * 2)
        game_h = 1920 - cam_h
        gw = int(1080 * 1080 / game_h) // 2 * 2
        base = (f"[0:v]scale=1920:1080,split=2[a][b];"
                f"[a]crop={w}:{h}:{x}:{y},scale=1080:{cam_h},format=yuv420p[cam];"
                f"[b]crop={gw}:1080:(1920-{gw})/2:0,scale=1080:{game_h},format=yuv420p[game];"
                f"[cam][game]vstack=inputs=2[st]")
        ov_y = cam_h - 80
    elif lay == "track":
        cmd = WORK / f"{name}.cmd"
        cmd.write_text("".join(f"{t:.3f} crop@trk x {x};\n" for t, x in box), encoding="utf-8")
        base = (f"[0:v]scale=1920:1080,sendcmd=f={cmd.name},crop@trk=608:1080:{box[0][1]}:0,"
                f"scale=1080:1920,format=yuv420p[st]")
        ov_y = 240   # unter der TikTok-/Reels-Kopfzeile
    else:
        base = "[0:v]scale=1920:1080,crop=608:1080:656:0,scale=1080:1920,format=yuv420p[st]"
        ov_y = 240
    if ov_file:
        fc = base + f";[1:v]scale=980:-2,format=rgba[ov];[st][ov]overlay=(W-w)/2:{ov_y}[o1];[o1]ass={ass.name}:fontsdir=fonts[v]"
    else:
        fc = base + f";[st]ass={ass.name}:fontsdir=fonts[v]"
    fonts = WORK / "fonts"
    fonts.mkdir(exist_ok=True)
    (fonts / "Anton-Regular.ttf").write_bytes((HERE / "fonts" / "Anton-Regular.ttf").read_bytes())
    inputs = ["-i", raw.name] + (["-i", str(ov_file.resolve())] if ov_file else [])
    sh(["ffmpeg", "-y", "-loglevel", "error", "-filter_threads", "1", *inputs,
        "-filter_complex", fc, "-map", "[v]", "-map", "0:a", *EXPORT, out.name], cwd=WORK)
    raw.unlink(missing_ok=True)
    problem = qa_check(out)
    if problem:
        print(f"  QA-Pruefung fehlgeschlagen ({problem}) - Clip verworfen", flush=True)
        out.unlink(missing_ok=True)
        return None, "", None
    return out, text, cam_layout


# TikTok/Instagram-taugliche Ausgabe: H.264 High@4.1, 30 fps konstant, Keyframe jede Sekunde (geschlossene GOP),
# BT.709, AAC 48 kHz Stereo, moov vorne. Lange GOPs (vorher 8 s) fuehrten in der TikTok-Vorschau zu Schwarzbild.
EXPORT = ["-r", "30", "-fps_mode", "cfr", "-c:v", "libx264", "-preset", "medium", "-profile:v", "high", "-level:v", "4.1",
          "-crf", "21", "-maxrate", "8M", "-bufsize", "16M", "-g", "30", "-keyint_min", "30", "-sc_threshold", "0",
          "-flags", "+cgop", "-pix_fmt", "yuv420p", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
          "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2", "-shortest", "-movflags", "+faststart"]


def qa_check(path):
    """Vor dem Upload: 1080x1920, Bild- und Tonspur gleich lang, keine Schwarz-/Standbild-Strecken, fehlerfrei decodierbar."""
    probe = lambda sel, ent: subprocess.run(["ffprobe", "-v", "error", "-select_streams", sel, "-show_entries", ent,
                                             "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout.strip()
    try:
        w, h = map(int, probe("v:0", "stream=width,height").split(",")[:2])
        vd = float(probe("v:0", "stream=duration") or 0)
        ad = float(probe("a:0", "stream=duration") or 0)
    except ValueError:
        return "nicht lesbar"
    if (w, h) != (1080, 1920):
        return f"Format {w}x{h}"
    if vd < 7 or abs(vd - ad) > 0.5:
        return f"Bild {vd:.1f}s / Ton {ad:.1f}s"
    full = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-vf", "blackdetect=d=1.0:pix_th=0.06,freezedetect=n=0.001:d=6",
                           "-f", "null", "-"], capture_output=True, text=True).stderr
    bad = re.search(r"error while decoding|Invalid data found|corrupt", full, re.I)
    if bad:
        return "Decoder-Fehler: " + bad.group(0)
    if "black_start" in full:
        return "Schwarzbild " + re.findall(r"black_start:[\d.]+", full)[0]
    if "freeze_start" in full:
        return "Standbild " + re.findall(r"freeze_start: [\d.]+", full)[0]
    return None


def queue(path, title, caption, seconds, camp=None):
    tok = os.environ.get("GGCUTS_UPLOAD_TOKEN", "").strip()
    if not tok:
        print("  kein GGCUTS_UPLOAD_TOKEN – nicht eingereiht")
        return None
    h = {"x-gaming-token": tok, "Content-Type": "application/json"}
    r = requests.post(UPLOAD, json={"action": "upload_url", "name": path.name}, headers=h, timeout=60).json()
    if "signed_url" not in r:
        raise RuntimeError(f"upload_url: {r}")
    with open(path, "rb") as f:
        up = requests.put(r["signed_url"], data=f, headers={"Content-Type": "video/mp4", "x-upsert": "true"}, timeout=600)
    if up.status_code >= 300:
        raise RuntimeError(f"Upload {up.status_code}: {up.text[:200]}")
    reg = requests.post(UPLOAD, json={"action": "register", "path": r["path"], "title": title, "caption": caption,
                                      "seconds": seconds,
                                      "cr_campaign": (camp or {}).get("name") if (camp or {}).get("content_rewards") else None,
                                      "cr_campaign_id": (camp or {}).get("content_rewards")}, headers=h, timeout=60).json()
    print("  eingereiht:", reg)
    return reg


def run_campaign(camp, count, test, st):
    done = 0
    vl = vods(camp)
    print(f"[{camp['key']}] {len(vl)} VODs:", [(v, int(d)) for v, _, d in vl[:4]], flush=True)
    for vod, title, dur in vl[:4]:
        info = st["vods"].setdefault(vod, {"title": title, "clips": []})
        info.setdefault("campaign", camp["key"])
        long_vod = bool(dur and dur > 6 * 3600)
        if (not long_vod and len(info["clips"]) >= MAX_PER_VOD) or (dur and dur < 1800):
            continue
        need = count - done
        if need <= 0:
            break
        print(f"VOD {vod}: {title} ({(dur or 0)/3600:.1f} h), bisher {len(info['clips'])} Clips", flush=True)
        # Sehr lange VODs (z. B. 48-h-Marathons) in 4-h-Abschnitten durchgehen, Fortschritt in scan_pos
        off = 0.0
        sect = []
        if dur and dur > 6 * 3600:
            off = float(info.get("scan_pos", 300))
            if off > dur - 1800:  # VOD ausgeschoepft
                continue
            end = min(dur - 60, off + 4 * 3600)
            sect = ["--download-sections", f"*{off:.0f}-{end:.0f}"]
            info["scan_pos"] = end
            print(f"  langer VOD: Abschnitt {off/3600:.1f}-{end/3600:.1f} h", flush=True)
        audio = WORK / f"{vod}.m4a"
        if not audio.exists():
            sh(["yt-dlp", "-q", "-f", "Audio_Only/worstaudio/ba", "-N", "8", *sect, "-o", str(audio), vod_url(camp, vod)])
        used = [(c["start"], c["end"]) for c in info["clips"]] + [tuple(x) for x in info.get("rejected", [])]
        ts = peaks(audio, skip=[(a - off, b - off) for a, b in used])
        cands = [(t + off, [(a + off, b + off, txt) for a, b, txt in transcribe_window(audio, t)]) for t in ts]
        ts = [t + off for t in ts]
        picks = choose(cands, need + 3, title, camp)
        for c in picks:
            if done >= count:
                break
            s, e = float(c["start"]), float(c["end"])
            if any(not (e <= us or s >= ue) for us, ue in used):
                continue
            out, text, cam = render(camp, vod, s - 0.3, e + 0.4, f"{camp['key']}_{vod}_{int(s)}", c.get("hook", ""))
            if out is None:
                info.setdefault("rejected", []).append([s, e])
                continue
            caption = f"{c.get('caption', '').strip()} {camp['caption_suffix']}"
            if not test:
                queue(out, f"{camp['name']}: {c.get('hook') or c.get('caption', '')[:50]}", caption, round(e - s, 1), camp)
            info["clips"].append({"start": s, "end": e, "hook": c.get("hook"), "caption": caption,
                                  "layout": "cam" if cam else "full", "date": dt.date.today().isoformat()})
            used.append((s, e))
            done += 1
            save_state(st)
        info.setdefault("rejected", []).extend([[t - 30, t + 25] for t in ts])
        save_state(st)
        audio.unlink(missing_ok=True)
        if done >= count:
            break
    return done


def kampagne_laeuft(cr_id):
    """Content-Rewards-Kampagne noch aktiv? (öffentliche Kampagnenseite: status + Budget). None = nicht prüfbar."""
    try:
        s = requests.get(f"https://contentrewards.com/c/campaigns/{cr_id}", headers={"User-Agent": "Mozilla/5.0"}, timeout=30).text
        s = s.replace('\\"', '"')
        i = s.find(f'"id":"{cr_id}"')
        if i < 0:
            return None
        seg = s[i:i + 20000]
        status = (re.search(r'"status":"([a-z_]+)"', seg) or [None, "unbekannt"])[1]
        spent = int((re.search(r'"budgetSpentCents":(\d+)', seg) or [None, 0])[1])
        budget = int((re.search(r'"budgetCents":(\d+)', seg) or [None, 0])[1])
        aktiv = status == "active" and (budget == 0 or spent < budget * 0.97)
        print(f"  Kampagne {cr_id[:8]}: {status}, {spent / 100:.0f}/{budget / 100:.0f} $ -> {'aktiv' if aktiv else 'BEENDET'}", flush=True)
        return aktiv
    except Exception as e:  # noqa: BLE001
        print("  Kampagnenprüfung fehlgeschlagen:", e)
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=0, help="Gesamtzahl (0 = per_day je Kampagne)")
    ap.add_argument("--only", default="", help="nur diese Kampagne (key)")
    ap.add_argument("--test", action="store_true", help="rendern, nicht einreihen")
    a = ap.parse_args()
    WORK.mkdir(exist_ok=True)
    st = load_state()
    camps = [c for c in CAMPAIGNS if c.get("active", True) and (not a.only or c["key"] == a.only)]
    # Seit 20.11.2026 (Robin): nur noch Clips für laufende Content-Rewards-Kampagnen – ohne Kampagne kein Geld, also kein Clip.
    laufend = []
    for c in camps:
        if not c.get("content_rewards"):
            print(f"[{c['key']}] keine Content-Rewards-Kampagne hinterlegt – übersprungen", flush=True)
            continue
        if kampagne_laeuft(c["content_rewards"]) is False:
            print(f"[{c['key']}] Kampagne beendet – übersprungen", flush=True)
            continue
        laufend.append(c)
    camps = laufend
    if not camps:
        print("keine laufende Kampagne – nichts zu tun")
        return
    total = 0
    for i, camp in enumerate(camps):
        n = camp.get("per_day", 1)
        if a.count:
            n = a.count // len(camps) + (1 if i < a.count % len(camps) else 0)
        if n <= 0:
            continue
        try:
            total += run_campaign(camp, n, a.test, st)
        except Exception as e:  # eine kaputte Kampagne soll die anderen nicht blockieren
            print(f"[{camp['key']}] Fehler: {e}", flush=True)
    print(f"fertig: {total} Clips")
    if total == 0:
        sys.exit("keine Clips erzeugt")


if __name__ == "__main__":
    main()
