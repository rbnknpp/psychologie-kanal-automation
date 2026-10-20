"""Baut das Einschlaf-Hypnose-Video: langsame Sprachausgabe mit Pausen, darunter
ein durchgehender Klangteppich (Rauschen + tiefe, schwebende Toene), dazu ein
ruhiges Standbild. Nach dem gesprochenen Teil laufen nur noch die Klaenge bis
zur Ziellaenge, am Ende wird ausgeblendet.

Alles lokal mit edge-tts und ffmpeg, keine Kosten und keine Lizenzfragen.
"""
import asyncio
import os
import subprocess
from pathlib import Path

import edge_tts
from PIL import Image, ImageDraw, ImageFilter, ImageFont

import config

SAMPLE_RATE = 48000


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, capture_output=True)


def _duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


async def _tts(text: str, out: Path) -> None:
    com = edge_tts.Communicate(text, config.HYPNOSIS_VOICE, rate=config.HYPNOSIS_RATE, pitch=config.HYPNOSIS_PITCH)
    await com.save(str(out))


def _tts_retry(text: str, out: Path, attempts: int = 3) -> None:
    if config.TTS_ENGINE == "piper":
        # Langsam und ruhig: length_scale > 1 dehnt die Sprechgeschwindigkeit.
        from tts import piper_engine
        wav = out.with_suffix(".piper.wav")
        piper_engine.speak_to_wav(text, str(wav), length_scale=1.35)
        _run(["ffmpeg", "-y", "-i", str(wav), "-ar", "24000", "-ac", "1", "-b:a", "96k", str(out)])
        return
    for i in range(1, attempts + 1):
        try:
            asyncio.run(_tts(text, out))
            return
        except Exception as e:
            print(f"  edge-tts Versuch {i}/{attempts} fehlgeschlagen: {e}")
    raise RuntimeError("edge-tts fuer Hypnose-Abschnitt fehlgeschlagen")


def build_voice_track(sections: list[dict], work: Path) -> tuple[Path, list[tuple[float, str]]]:
    """Spricht jeden Abschnitt einzeln ein und haengt danach die gewuenschte Stille an.
    Gibt die Sprachspur (WAV) und Kapitelmarken [(sekunde, phase)] zurueck."""
    work.mkdir(parents=True, exist_ok=True)
    parts, chapters, t, last_phase = [], [], 0.0, None
    for i, sec in enumerate(sections, start=1):
        mp3 = work / f"s{i:03d}.mp3"
        wav = work / f"s{i:03d}.wav"
        _tts_retry(sec["text"], mp3)
        # Einheitliches Format, leicht abgerundet und waermer (Hoehen etwas absenken)
        _run(["ffmpeg", "-y", "-i", str(mp3), "-af", "lowpass=f=7000,volume=1.0", "-ar", str(SAMPLE_RATE), "-ac", "2", str(wav)])
        if sec.get("phase") and sec["phase"] != last_phase:
            chapters.append((t, sec["phase"]))
            last_phase = sec["phase"]
        parts.append(wav)
        t += _duration(wav)
        pause = max(2.0, min(25.0, float(sec.get("pause_after_seconds", 6))))
        sil = work / f"p{i:03d}.wav"
        _run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r={SAMPLE_RATE}:cl=stereo", "-t", f"{pause:.2f}", str(sil)])
        parts.append(sil)
        t += pause
    listfile = work / "voice_list.txt"
    listfile.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
    voice = work / "voice.wav"
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listfile), "-c", "copy", str(voice)])
    return voice, chapters


def build_still(text: str, out: Path, size=(1920, 1080)) -> Path:
    """Ruhiges Standbild: tiefes Nachtblau, weicher Mondschein-Schimmer, dezente Schrift."""
    w, h = size
    img = Image.new("RGB", size, (8, 12, 30))
    glow = Image.new("RGB", size, (0, 0, 0))
    d = ImageDraw.Draw(glow)
    for r, c in [(520, (40, 50, 110)), (360, (60, 70, 140)), (220, (95, 100, 170))]:
        d.ellipse([w * 0.68 - r, h * 0.32 - r, w * 0.68 + r, h * 0.32 + r], fill=c)
    glow = glow.filter(ImageFilter.GaussianBlur(160))
    img = Image.blend(img, glow, 0.55)
    d = ImageDraw.Draw(img)
    d.ellipse([w * 0.68 - 70, h * 0.32 - 70, w * 0.68 + 70, h * 0.32 + 70], fill=(225, 228, 245))
    try:
        font_file = "C:/Windows/Fonts/segoeuil.ttf" if os.name == "nt" else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"
        font = ImageFont.truetype(font_file, 64)
        small = ImageFont.truetype(font_file, 34)
    except OSError:
        font = small = ImageFont.load_default()
    d.text((140, h - 300), text, font=font, fill=(205, 210, 235))
    d.text((140, h - 210), config.INTRO_CHANNEL_NAME, font=small, fill=(120, 128, 175))
    img.save(out, quality=95)
    return out


def render_hypnosis(voice: Path, still: Path, out: Path, total_seconds: int, lead_in: float = 15.0,
                    music: Path = None) -> Path:
    """Mischt Sprachspur ueber Musik + leises Rauschen und rendert das Video mit Standbild.
    music: nahtlose Schleife aus video.ambient_music (wird bis zur Ziellaenge wiederholt).
    Ohne music: Rauschen + zwei schwebende Sinustoene. Einblenden 10 s, Ausblenden 120 s."""
    T = int(total_seconds)
    delay_ms = int(lead_in * 1000)
    fade_out_start = max(0, T - 120)
    if music:
        filter_complex = (
            f"anoisesrc=color=brown:amplitude=0.5:sample_rate={SAMPLE_RATE}:duration={T},"
            f"lowpass=f=380,volume=0.10,aformat=channel_layouts=stereo[noise];"
            f"[2:a]aresample={SAMPLE_RATE},aformat=channel_layouts=stereo,volume=0.55[music];"
            f"[1:a]adelay={delay_ms}|{delay_ms},volume=1.6,apad[voice];"
            f"[noise][music][voice]amix=inputs=3:duration=first:normalize=0,"
            f"afade=t=in:d=10,afade=t=out:st={fade_out_start}:d=120,alimiter=limit=0.9[a]"
        )
        _run([
            "ffmpeg", "-y",
            "-loop", "1", "-framerate", "2", "-i", str(still),
            "-i", str(voice),
            "-stream_loop", "-1", "-i", str(music),
            "-filter_complex", filter_complex,
            "-map", "0:v", "-map", "[a]",
            "-t", str(T),
            "-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage", "-crf", "26", "-pix_fmt", "yuv420p", "-r", "2",
            "-c:a", "aac", "-b:a", "160k",
            "-movflags", "+faststart",
            str(out),
        ])
        return out
    filter_complex = (
        f"anoisesrc=color=brown:amplitude=0.5:sample_rate={SAMPLE_RATE}:duration={T},"
        f"lowpass=f=420,volume=0.30,aformat=channel_layouts=stereo[noise];"
        f"sine=frequency=110:sample_rate={SAMPLE_RATE}:duration={T},tremolo=f=0.1:d=0.6,volume=0.035,aformat=channel_layouts=stereo[pad1];"
        f"sine=frequency=164.81:sample_rate={SAMPLE_RATE}:duration={T},tremolo=f=0.13:d=0.7,volume=0.025,aformat=channel_layouts=stereo[pad2];"
        f"[1:a]adelay={delay_ms}|{delay_ms},volume=1.6,apad[voice];"
        f"[noise][pad1][pad2][voice]amix=inputs=4:duration=first:normalize=0,"
        f"afade=t=in:d=10,afade=t=out:st={fade_out_start}:d=120,alimiter=limit=0.9[a]"
    )
    _run([
        "ffmpeg", "-y",
        "-loop", "1", "-framerate", "2", "-i", str(still),
        "-i", str(voice),
        "-filter_complex", filter_complex,
        "-map", "0:v", "-map", "[a]",
        "-t", str(T),
        "-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage", "-crf", "26", "-pix_fmt", "yuv420p", "-r", "2",
        "-c:a", "aac", "-b:a", "160k",
        "-movflags", "+faststart",
        str(out),
    ])
    return out
