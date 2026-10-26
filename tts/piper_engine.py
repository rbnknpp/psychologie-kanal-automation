"""Kostenlose Offline-Sprachausgabe mit Piper (deutsche Stimme, laeuft ueberall,
kein Konto, kein Netz zur Laufzeit, wird nicht von Cloud-IP-Sperren getroffen).

Piper liefert keine Wort-Zeitstempel. Darum wird satzweise gesprochen: die Dauer
jedes Satzes ist exakt bekannt, innerhalb eines Satzes werden die Woerter
proportional zur Zeichenzahl verteilt (Sätze haben hoechstens ~15 Woerter, der
Fehler bleibt unter ca. 0.3 s).
"""
import os
import re
import tempfile
import wave
from pathlib import Path

PIPER_MODEL = os.getenv("PIPER_MODEL", "de_DE-thorsten-medium")
# Feinabstimmung der Sprechweise (Standard von Piper: 1.0 / 0.667 / 0.8)
LENGTH_SCALE = float(os.getenv("PIPER_LENGTH_SCALE", "1.0"))   # >1 = langsamer
NOISE_SCALE = float(os.getenv("PIPER_NOISE_SCALE", "0.667"))   # mehr = lebendigere Betonung
NOISE_W = float(os.getenv("PIPER_NOISE_W", "0.8"))             # mehr = variablere Satzmelodie/Pausen
PIPER_MODEL_DIR = Path(os.getenv("PIPER_MODEL_DIR", str(Path(__file__).resolve().parent.parent / "assets" / "voices")))
SENTENCE_PAUSE = 0.28  # Sekunden Stille nach einem Satz

_voice = None


def available() -> bool:
    return (PIPER_MODEL_DIR / f"{PIPER_MODEL}.onnx").exists()


def _load():
    global _voice
    if _voice is None:
        from piper import PiperVoice
        _voice = PiperVoice.load(str(PIPER_MODEL_DIR / f"{PIPER_MODEL}.onnx"))
    return _voice


def _write_wav(text: str, wav_path: str, length_scale: float) -> None:
    from piper import SynthesisConfig
    with wave.open(wav_path, "wb") as wf:
        _load().synthesize_wav(
            text, wf,
            syn_config=SynthesisConfig(length_scale=length_scale * LENGTH_SCALE, noise_scale=NOISE_SCALE, noise_w_scale=NOISE_W),
        )


def speak_to_wav(text: str, wav_path: str, length_scale: float = 1.0) -> None:
    """Spricht `text` (beliebig lang) in eine WAV-Datei, z.B. fuer die Hypnose-Abschnitte."""
    _write_wav(text, wav_path, length_scale)


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?…])\s+", text.strip())
    return [p for p in (s.strip() for s in parts) if p]


def synthesize_with_words(text: str, out_wav: str, length_scale: float = 1.0) -> tuple[list[dict], float]:
    """Satzweise Sprachausgabe -> eine WAV-Datei + Wort-Zeitstempel. Rueckgabe: (words, dauer)."""
    sentences = split_sentences(text) or [text]
    words, t = [], 0.0
    chunks: list[tuple[bytes, int, int, int]] = []
    with tempfile.TemporaryDirectory() as tmp:
        for i, sent in enumerate(sentences):
            p = os.path.join(tmp, f"s{i}.wav")
            _write_wav(sent, p, length_scale)
            with wave.open(p, "rb") as w:
                frames = w.readframes(w.getnframes())
                params = (w.getframerate(), w.getsampwidth(), w.getnchannels())
                dur = w.getnframes() / w.getframerate()
            chunks.append((frames, *params))
            toks = sent.split()
            weights = [max(1, len(re.sub(r"\W", "", tok))) for tok in toks]
            total_w = sum(weights)
            cursor = t
            for tok, wgt in zip(toks, weights):
                span = dur * wgt / total_w
                words.append({"text": tok, "start": cursor, "end": cursor + span})
                cursor += span
            t += dur + SENTENCE_PAUSE
    rate, width, ch = chunks[0][1:]
    silence = b"\x00" * (int(rate * SENTENCE_PAUSE) * width * ch)
    with wave.open(out_wav, "wb") as out:
        out.setframerate(rate)
        out.setsampwidth(width)
        out.setnchannels(ch)
        for k, (frames, *_p) in enumerate(chunks):
            out.writeframes(frames)
            if k < len(chunks) - 1:
                out.writeframes(silence)
    return words, t - SENTENCE_PAUSE
