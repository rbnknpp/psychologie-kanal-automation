"""Kostenlose Text-zu-Sprache ueber Microsofts Edge-TTS-Engine (edge-tts).

Kein API-Key, kein Zeichen-Limit. Liefert Wort-genaue Timestamps fuer
synchronisierte Untertitel (wie zuvor ElevenLabs, nur ohne Kosten).
"""
import asyncio
import json
import subprocess
import tempfile
import time
from pathlib import Path

import edge_tts

import config


async def _synthesize_async(text: str, out_path: str) -> list[dict]:
    communicate = edge_tts.Communicate(text, config.EDGE_TTS_VOICE, boundary="WordBoundary")
    words = []
    with open(out_path, "wb") as f:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                start = chunk["offset"] / 10_000_000  # 100ns-Ticks -> Sekunden
                dur = chunk["duration"] / 10_000_000
                words.append({"text": chunk["text"], "start": start, "end": start + dur})
    return words


def _get_duration(path: str) -> float:
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", path,
        ],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def _synthesize_piper(text: str, out_path: str) -> dict:
    from tts import piper_engine
    with tempfile.TemporaryDirectory() as tmp:
        wav = str(Path(tmp) / "t.wav")
        words, _ = piper_engine.synthesize_with_words(text, wav)
        subprocess.run(
            ["ffmpeg", "-y", "-i", wav, "-ar", "24000", "-ac", "1", "-b:a", "96k", out_path],
            check=True, capture_output=True,
        )
    return {"words": words, "duration": _get_duration(out_path)}


# Nach einem vollstaendig gescheiterten edge-tts-Versuch wird der Dienst fuer den Rest
# des Prozesses uebersprungen (sonst verliert jeder Clip ~3 Minuten mit Retries).
_edge_broken = False


def _synthesize_sapi(text: str, out_path: str) -> dict:
    """Offline-Notfall-Fallback ueber Windows SAPI (deutsche Stimme), falls Microsofts
    Edge-TTS-Dienst dauerhaft 403 liefert (live am 12./14.10.2026 beobachtet).
    Klingt schlechter, haelt aber den taeglichen Upload am Laufen."""
    with tempfile.TemporaryDirectory() as tmp:
        txt, wav, js = Path(tmp) / "t.txt", Path(tmp) / "t.wav", Path(tmp) / "t.json"
        txt.write_text(text, encoding="utf-8")
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
             str(Path(__file__).with_name("sapi_speak.ps1")), str(txt), str(wav), str(js)],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(wav), "-ar", "24000", "-ac", "1", "-b:a", "96k", out_path],
            check=True, capture_output=True,
        )
        starts = json.loads(js.read_text(encoding="utf-8"))
    duration = _get_duration(out_path)
    words = []
    for i, w in enumerate(starts):
        end = starts[i + 1]["start"] if i + 1 < len(starts) else duration
        words.append({"text": w["text"], "start": w["start"], "end": max(end, w["start"] + 0.1)})
    return {"words": words, "duration": duration}


def synthesize(text: str, out_path: str, max_attempts: int = 5) -> dict:
    """Erzeugt eine MP3-Datei aus `text` und gibt Wort-Timestamps zurueck.

    Return: {"words": [{"text", "start", "end"}, ...], "duration": float}
    Die Gesamtdauer wird per ffprobe aus der tatsaechlichen Audiodatei
    gemessen (die Wort-Events enden oft etwas vor dem echten Dateiende).

    edge-tts ist ein kostenloser, inoffizieller Dienst und faellt gelegentlich
    kurz aus (z.B. "NoAudioReceived") - daher mit Retry gegen transiente Fehler.
    """
    global _edge_broken
    if config.TTS_ENGINE == "piper":
        try:
            return _synthesize_piper(text, out_path)
        except Exception as e:
            print(f"  Piper fehlgeschlagen ({e}) - weiche auf edge-tts aus")
    last_error = None
    for attempt in range(1, 0 if _edge_broken else max_attempts + 1):
        try:
            words = asyncio.run(_synthesize_async(text, out_path))
            duration = _get_duration(out_path)
            return {"words": words, "duration": duration}
        except Exception as e:
            last_error = e
            print(f"  edge-tts Versuch {attempt}/{max_attempts} fehlgeschlagen: {e}")
            if attempt < max_attempts:
                time.sleep(5 * 3 ** (attempt - 1))  # 5s, 15s, 45s, 135s

    if not _edge_broken:
        print(f"  edge-tts nach {max_attempts} Versuchen fehlgeschlagen ({str(last_error)[:80]}) - nutze ab jetzt Windows-SAPI-Fallback")
    _edge_broken = True
    try:
        return _synthesize_sapi(text, out_path)
    except Exception as e:
        raise RuntimeError(f"edge-tts und SAPI-Fallback fehlgeschlagen: {last_error} / {e}")
