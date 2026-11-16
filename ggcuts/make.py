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

HERE = Path(__file__).parent
WORK = HERE / "work"
STATE = HERE / "state.json"
OVERLAY = HERE / "overlay_skiimasktimmy.png"
CHANNEL = "skiimasktimmy"
UPLOAD = "https://kfiiaqjzzwwacthzrcut.supabase.co/functions/v1/gaming-upload"
MAX_PER_VOD = 9
CAM = (22, 772, 420, 226)  # Facecam-Box im 1920x1080-Stream (Gameplay-Szene)
TAGS = "#skiimasktimmy #twitchclips #fps #gaming"


def sh(cmd, **kw):
    print("$", " ".join(str(c) for c in cmd)[:200], flush=True)
    return subprocess.run(cmd, check=True, **kw)


def load_state():
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {"vods": {}}


def save_state(st):
    STATE.write_text(json.dumps(st, indent=1, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------- VOD + Kandidaten
def vods():
    out = subprocess.run(["yt-dlp", "--flat-playlist", "-J",
                          f"https://www.twitch.tv/{CHANNEL}/videos?filter=archives&sort=time"],
                         capture_output=True, text=True, encoding="utf-8", check=True).stdout
    return [(e["id"].lstrip("v"), e.get("title", ""), e.get("duration") or 0) for e in json.loads(out)["entries"]]


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


def choose(cands, n, title):
    import anthropic
    lines = []
    for i, (t, segs) in enumerate(cands):
        lines.append(f"### Kandidat {i} (Lautstaerke-Spitze bei {t:.0f}s)")
        lines += [f"[{a:.1f}-{b:.1f}] {txt}" for a, b, txt in segs] or ["(kaum Sprache)"]
    prompt = f"""You pick TikTok clips from a Twitch VOD of streamer SkiiMaskTimmy (stream title: {title!r}).
Campaign formats that perform: clutch plays, extraction tension, funny moments & fails, first impressions / hot takes,
genuine reactions (jump scares, disbelief, hype), community moments (chat banter, giveaways).
Avoid: sponsor reads, ads, bathroom breaks, menus/loading, quiet talk, anything that needs long context.

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
def ass_time(t):
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def subtitles(src, ass, hook, sub_y):
    segs, _ = whisper().transcribe(str(src), language="en", word_timestamps=True, vad_filter=True)
    words = [w for s in segs for w in s.words if w.word.strip()]
    head = f"""[Script Info]
PlayResX: 1080
PlayResY: 1920
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: W,Anton,96,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,0,0,0,0,100,100,1,0,1,6,2,5,40,40,0,1
Style: H,Anton,76,&H0000E6FF,&H0000E6FF,&H00000000,&H96000000,0,0,0,0,100,100,1,0,3,5,0,5,60,60,0,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = []
    if hook:
        ev.append(f"Dialogue: 1,{ass_time(0)},{ass_time(3.2)},H,,0,0,0,,{{\\pos(540,{sub_y - 650})}}{hook.upper()}")
    for i in range(0, len(words), 3):
        grp = words[i:i + 3]
        end = grp[-1].end + 0.15
        if i + 3 < len(words):
            end = min(end, words[i + 3].start)
        for k, w in enumerate(grp):
            we = grp[k + 1].start if k + 1 < len(grp) else end
            txt = " ".join(("{\\c&H0000E6FF&}" + g.word.strip().upper() + "{\\c&H00FFFFFF&}") if g is w
                           else g.word.strip().upper() for g in grp)
            ev.append(f"Dialogue: 0,{ass_time(w.start)},{ass_time(max(we, w.start + 0.05))},W,,0,0,0,,"
                      f"{{\\pos(540,{sub_y})}}{txt}")
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


def layout(video, dur):
    """'cam' = Gameplay mit Facecam-Box unten links, 'full' = Kamera bildfuellend (Just Chatting),
    None = keine Facecam sichtbar (Desktop, Menue, Pause) -> Clip verwerfen (Kampagnenregel: Facecam immer sichtbar)."""
    votes = []
    for i in range(5):
        t = max(0.5, min(dur - 1, 0.5 + i * (dur - 1.5) / 4))
        im = frame_at(video, round(t, 1), f"probe{i}.jpg")
        # tuerkiser (animiert leuchtender) oberer Rahmen der Facecam-Box bei y~766-770, an 18 Stellen pruefen
        band = im[758:780].astype(int)
        hits = sum(1 for x in range(60, 420, 20)
                   if any(px[1] > 150 and px[2] > 150 and px[1] - px[0] > 35 for px in band[:, x]))
        box = hits >= 6
        center = len(faces(im[:, 560:1360], min_size=120, neighbors=5)) > 0
        votes.append("cam" if box else "full" if center else None)
        print(f"  Probe {t:.0f}s -> {votes[-1]}", flush=True)
    if votes.count("cam") >= 3:
        return "cam"
    if votes.count("full") >= 3:
        return "full"
    return None


def render(vod, start, end, name, hook):
    raw = WORK / f"{name}_raw.mp4"
    out = WORK / f"{name}.mp4"
    sh(["yt-dlp", "--no-progress", "-f", "b[vcodec!=none][height<=1080]/b[vcodec!=none]", "-S", "res:1080,fps",
        "--download-sections", f"*{start:.1f}-{end:.1f}", "--force-keyframes-at-cuts", "-o", str(raw),
        f"https://www.twitch.tv/videos/{vod}"])
    lay = layout(raw, end - start)
    if lay is None:
        print("  keine Facecam sichtbar - Clip verworfen", flush=True)
        raw.unlink(missing_ok=True)
        return None, "", None
    cam_layout = lay == "cam"
    ass = WORK / f"{name}.ass"
    text = subtitles(raw, ass, hook, 1430 if cam_layout else 1500)
    x, y, w, h = CAM
    if cam_layout:
        fc = (f"[0:v]scale=1920:1080,split=2[a][b];"
              f"[a]crop={w}:{h}:{x}:{y},scale=1080:582,format=yuv420p[cam];"
              f"[b]crop=872:1080:524:0,scale=1080:1338,format=yuv420p[game];"
              f"[cam][game]vstack=inputs=2[st];[1:v]scale=980:-2,format=rgba[ov];"
              f"[st][ov]overlay=(W-w)/2:502[o1];[o1]ass={ass.name}:fontsdir=fonts[v]")
    else:
        fc = (f"[0:v]scale=1920:1080,crop=608:1080:656:0,scale=1080:1920,format=yuv420p[st];"
              f"[1:v]scale=980:-2,format=rgba[ov];[st][ov]overlay=(W-w)/2:180[o1];[o1]ass={ass.name}:fontsdir=fonts[v]")
    fonts = WORK / "fonts"
    fonts.mkdir(exist_ok=True)
    (fonts / "Anton-Regular.ttf").write_bytes((HERE / "fonts" / "Anton-Regular.ttf").read_bytes())
    sh(["ffmpeg", "-y", "-loglevel", "error", "-filter_threads", "1", "-i", raw.name, "-i", str(OVERLAY.resolve()),
        "-filter_complex", fc, "-map", "[v]", "-map", "0:a", "-r", "30", "-c:v", "libx264", "-preset", "medium",
        "-crf", "21", "-maxrate", "6M", "-bufsize", "12M", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
        out.name], cwd=WORK)
    raw.unlink(missing_ok=True)
    return out, text, cam_layout


def queue(path, title, caption, seconds):
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
                                      "seconds": seconds}, headers=h, timeout=60).json()
    print("  eingereiht:", reg)
    return reg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=3)
    ap.add_argument("--test", action="store_true", help="rendern, nicht einreihen")
    a = ap.parse_args()
    WORK.mkdir(exist_ok=True)
    st = load_state()
    done = 0
    vl = vods()
    print(f"{len(vl)} VODs gefunden:", [(v, int(d)) for v, _, d in vl[:4]], flush=True)
    for vod, title, dur in vl[:4]:
        info = st["vods"].setdefault(vod, {"title": title, "clips": []})
        if len(info["clips"]) >= MAX_PER_VOD or (dur and dur < 1800):
            print(f"  VOD {vod} übersprungen ({len(info['clips'])} Clips, {dur}s)")
            continue
        need = a.count - done
        if need <= 0:
            break
        print(f"VOD {vod}: {title} ({(dur or 0)/3600:.1f} h), bisher {len(info['clips'])} Clips", flush=True)
        audio = WORK / f"{vod}.m4a"
        if not audio.exists():
            sh(["yt-dlp", "-q", "-f", "Audio_Only/worstaudio", "-N", "8", "-o", str(audio), f"https://www.twitch.tv/videos/{vod}"])
        used = [(c["start"], c["end"]) for c in info["clips"]] + [tuple(x) for x in info.get("rejected", [])]
        ts = peaks(audio, skip=used)
        cands = [(t, transcribe_window(audio, t)) for t in ts]
        picks = choose(cands, need + 3, title)
        for c in picks:
            s, e = float(c["start"]), float(c["end"])
            if any(not (e <= us or s >= ue) for us, ue in used):
                continue
            name = f"timmy_{vod}_{int(s)}"
            if done >= a.count:
                break
            out, text, cam = render(vod, s - 0.3, e + 0.4, name, c.get("hook", ""))
            if out is None:
                info.setdefault("rejected", []).append([s, e])
                continue
            caption = f"{c.get('caption', '').strip()} twitch.tv/{CHANNEL} {TAGS}"
            secs = round(e - s, 1)
            if not a.test:
                queue(out, c.get("hook") or c.get("caption", "")[:60], caption, secs)
            info["clips"].append({"start": s, "end": e, "hook": c.get("hook"), "caption": caption,
                                  "layout": "cam" if cam else "full", "date": dt.date.today().isoformat()})
            used.append((s, e))
            done += 1
            save_state(st)
        info.setdefault("rejected", []).extend([[t - 30, t + 25] for t in ts])  # Kandidaten nicht erneut pruefen
        save_state(st)
        audio.unlink(missing_ok=True)
        if done >= a.count:
            break
    print(f"fertig: {done} Clips")
    if done == 0:
        sys.exit("keine Clips erzeugt")


if __name__ == "__main__":
    main()
