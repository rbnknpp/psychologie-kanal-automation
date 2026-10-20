"""Erzeugt ruhige Hintergrundmusik fuer das Entspannungsvideo: weiche Akkordflaechen,
darueber eine langsame, sparsame Melodie (Pentatonik, glockenartige Toene) mit Hall.

Rein synthetisch mit numpy - keine Lizenzfragen. Das Stueck ist eine nahtlose
Schleife (der Hall-Ausklang am Ende wird auf den Anfang gelegt), ffmpeg wiederholt
sie dann bis zur vollen Videolaenge. Jedes Video bekommt eine eigene Tonart und Melodie.
"""
import random
import wave
from pathlib import Path

import numpy as np

SR = 24000
CHORD_SECONDS = 16.0

# Ruhige Akkordfolgen (Halbtoene relativ zum Grundton), alle in Dur/Moll ohne Spannung
_PROGRESSIONS = [
    [[0, 4, 7, 11], [-3, 0, 4, 7], [-7, -3, 0, 4], [-5, -1, 2, 7]],   # Imaj7 - vi7 - IVmaj7 - V
    [[0, 4, 7, 14], [-5, -1, 2, 7], [-3, 0, 4, 9], [-7, -3, 0, 7]],   # I - V - vi - IV
    [[0, 3, 7, 10], [-4, 0, 3, 7], [-7, -4, 0, 3], [-2, 2, 5, 9]],    # i7 - VI - iv - VII (Moll)
]
_PENTA_MAJOR = [0, 2, 4, 7, 9]
_PENTA_MINOR = [0, 3, 5, 7, 10]


def _hz(semitone_from_a4: float) -> float:
    return 440.0 * 2 ** (semitone_from_a4 / 12)


def _pad_note(freq: float, length: float) -> np.ndarray:
    """Weiche Flaeche: drei leicht verstimmte Sinus + Oktave, langsames Ein-/Ausblenden."""
    t = np.arange(int(length * SR)) / SR
    sig = sum(np.sin(2 * np.pi * freq * d * t + p) for d, p in [(1.0, 0), (1.003, 1.1), (0.997, 2.3)])
    sig += 0.25 * np.sin(2 * np.pi * freq * 2 * t)
    attack, release = 4.0, 6.0
    env = np.minimum(1.0, t / attack) * np.clip((length - t) / release, 0, 1)
    return (sig * env / 3.3).astype(np.float32)


def _bell(freq: float, length: float = 7.0) -> np.ndarray:
    """Glockenartiger, sanfter Ton mit langem Ausklang."""
    t = np.arange(int(length * SR)) / SR
    sig = np.sin(2 * np.pi * freq * t) + 0.3 * np.sin(2 * np.pi * freq * 2 * t) * np.exp(-t * 1.5)
    env = np.minimum(1.0, t / 0.04) * np.exp(-t * 0.6)
    return (sig * env).astype(np.float32)


def _reverb(x: np.ndarray, loop_len: int) -> np.ndarray:
    """Einfacher Hall aus mehreren abklingenden Echos; wickelt um die Schleifenlaenge."""
    out = x.copy()
    for delay_s, gain in [(0.37, 0.35), (0.61, 0.28), (0.89, 0.22), (1.31, 0.16), (1.83, 0.11), (2.6, 0.07)]:
        out += gain * np.roll(x, int(delay_s * SR))
    return out


def build_music_loop(out: Path, seed: str, minutes: float = 8.0) -> Path:
    """Schreibt eine nahtlos wiederholbare Musik-Schleife (WAV, mono, 24 kHz)."""
    rng = random.Random(seed)
    root = rng.choice([-9, -7, -5, -4, -2])            # C, D, E, F, G (eine Oktave unter A4)
    prog = rng.choice(_PROGRESSIONS)
    scale = _PENTA_MINOR if prog is _PROGRESSIONS[2] else _PENTA_MAJOR
    n_chords = max(4, int(minutes * 60 / CHORD_SECONDS) // 4 * 4)
    loop_len = int(n_chords * CHORD_SECONDS * SR)
    buf = np.zeros(loop_len + 12 * SR, dtype=np.float32)

    # Akkordflaechen (ueberlappend, damit die Wechsel weich sind)
    for c in range(n_chords):
        chord = prog[c % len(prog)]
        start = int(c * CHORD_SECONDS * SR)
        for st in chord:
            note = _pad_note(_hz(root - 12 + st), CHORD_SECONDS + 6)
            buf[start:start + len(note)] += 0.16 * note

    # Melodie: wenige Toene pro Akkord, langsame Schritte, ab und zu Pausen
    deg = 2
    for c in range(n_chords):
        start_s = c * CHORD_SECONDS
        t = start_s + rng.uniform(0.5, 2.5)
        while t < start_s + CHORD_SECONDS - 2:
            if rng.random() < 0.8:
                deg = max(0, min(9, deg + rng.choice([-2, -1, -1, 0, 1, 1, 2])))
                semis = root + scale[deg % 5] + 12 * (deg // 5)
                note = _bell(_hz(semis))
                i = int(t * SR)
                buf[i:i + len(note)] += 0.10 * note[: len(buf) - i]
            t += rng.choice([2.0, 3.0, 4.0, 4.0, 6.0])

    # Ausklang ueber das Schleifenende hinaus auf den Anfang legen -> nahtlos
    tail = buf[loop_len:]
    loop = buf[:loop_len].copy()
    loop[: len(tail)] += tail
    loop = _reverb(loop, loop_len)
    loop *= 0.5 / max(1e-6, float(np.max(np.abs(loop))))

    pcm = (loop * 32767).astype(np.int16)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return out
