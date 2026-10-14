"""Video-Rendering ueber ffmpeg: Hintergrund-Clip + Audio + eingebrannte Untertitel."""
import subprocess
import time
from pathlib import Path

import config


def _escape_ffmpeg_path(path: str) -> str:
    # ffmpeg-Filtergraph-Syntax: Doppelpunkte und Backslashes muessen escaped werden
    p = str(Path(path).resolve()).replace("\\", "/")
    p = p.replace(":", r"\:")
    return p


def _run_ffmpeg(cmd: list[str], retries: int = 2) -> None:
    """ffmpeg mit Wiederholung bei transienten Abstuerzen (z.B. STATUS_STACK_OVERFLOW
    unter hoher Systemlast, live beobachtet) - ein einzelner Aussetzer soll nicht
    den kompletten Tageslauf abbrechen."""
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            subprocess.run(cmd, check=True, capture_output=True)
            return
        except subprocess.CalledProcessError as e:
            last_error = e
            print(f"  ffmpeg Versuch {attempt}/{retries} fehlgeschlagen (exit {e.returncode}), warte kurz...")
            if attempt < retries:
                time.sleep(10)
    raise last_error


def render_segment(
    background_path: str | list[str],
    audio_path: str,
    ass_path: str,
    out_path: str,
    resolution: tuple[int, int],
    duration: float,
    hook_zoom: bool = False,
    cover_path: str | None = None,
    cover_seconds: float = 0.45,
) -> str:
    width, height = resolution
    ass_esc = _escape_ffmpeg_path(ass_path)

    # Mehrere Hintergrund-Clips werden gleichmaessig ueber die Laenge verteilt
    # und hintereinander geschnitten (Abwechslung statt einer Endlosschleife).
    backgrounds = [background_path] if isinstance(background_path, str) else list(background_path)
    k = len(backgrounds)
    total = duration + 0.3
    per_clip = total / k
    audio_idx = k
    cover_idx = k + 1

    # Leicht groesser skalieren und wieder auf Zielgroesse zuschneiden ("Zoom"),
    # plus dezente Farbkorrektur/Vignette: veraendert Framing und Bildwerte des
    # Rohclips spuerbar genug, um Content-ID-Fingerprint-Treffer auf generisches
    # Stock-Footage unwahrscheinlicher zu machen (Pexels-Clips sind lizenziert,
    # tauchen aber manchmal zufaellig auch in kommerziellen Produktionen auf).
    zoom_w = (int(width * 1.1) // 2) * 2
    zoom_h = (int(height * 1.1) // 2) * 2

    # Optionaler "Zoom-Punch" in den ersten 0.4s: Bild startet 18% gezoomt und
    # zieht sich auf Normalansicht zurueck - visueller Pattern-Interrupt fuer
    # bessere Shorts-Retention in der kritischen ersten Sekunde.
    hook_stage = (
        f"scale=w='iw*if(lt(t,0.4),1.18-0.18*(t/0.4),1)':h=-2:eval=frame,crop={width}:{height},"
        if hook_zoom
        else ""
    )

    # Optionales Cover: liegt in den ersten Frames ueber dem Video (Audio/Timing
    # bleiben unveraendert), damit YouTubes Shorts-Kachel das gebrandete Bild zeigt.
    ass_tail = (
        f"ass='{ass_esc}'[pre];"
        f"[{cover_idx}:v]scale={width}:{height},setsar=1,format=yuv420p[cov];"
        f"[pre][cov]overlay=0:0:enable='lt(t,{cover_seconds})'[v]"
        if cover_path
        else f"ass='{ass_esc}'[v]"
    )

    prep = "".join(
        f"[{i}:v]scale={zoom_w}:{zoom_h}:force_original_aspect_ratio=increase,"
        f"crop={zoom_w}:{zoom_h},crop={width}:{height},setsar=1,fps={config.FPS}[c{i}];"
        for i in range(k)
    )
    if k > 1:
        joined = "".join(f"[c{i}]" for i in range(k)) + f"concat=n={k}:v=1:a=0[bg];"
        src = "[bg]"
    else:
        joined, src = "", "[c0]"

    filter_complex = (
        f"{prep}{joined}{src}"
        f"{hook_stage}"
        f"eq=contrast=1.05:saturation=1.12:brightness=0.01,vignette=PI/5,"
        f"setsar=1,"
        f"{ass_tail}"
    )

    bg_inputs = []
    for p in backgrounds:
        bg_inputs += ["-stream_loop", "-1", "-t", f"{per_clip:.3f}", "-i", p]
    cover_input = ["-loop", "1", "-framerate", str(config.FPS), "-i", str(cover_path)] if cover_path else []

    cmd = [
        "ffmpeg", "-y",
        *bg_inputs,
        "-i", audio_path,
        *cover_input,
        "-filter_complex", filter_complex,
        "-map", "[v]", "-map", f"{audio_idx}:a",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        "-t", f"{total:.2f}",
        "-shortest",
        out_path,
    ]
    _run_ffmpeg(cmd)
    return out_path


def concat_segments(segment_paths: list[str], out_path: str) -> str:
    # WICHTIG: "concat"-Filter (pro Datei ein eigener -i) statt "-f concat"-Demuxer
    # mit Stream-Copy oder Neu-Enkodierung ueber eine Listendatei. Stream-Copy
    # haengt nur die rohen Pakete aneinander und fuehrt bei uns zuverlaessig zu
    # Zeitstempel-Sprüngen an den Segment-Grenzen, die lokal via ffprobe/ffplay
    # unauffaellig bleiben, aber YouTubes Verarbeitung regelmaessig mit
    # "Verarbeitung abgebrochen / Datei gekuerzt oder ungueltig" ablehnt (live
    # beobachtet bei mehreren Longform-Kompilationen). Der "-f concat"-Demuxer
    # behaelt aber selbst beim Neu-Enkodieren AAC-Decoder-Zustand ueber
    # Dateigrenzen hinweg bei und produziert an Segmentuebergaengen kaputte
    # Audiodecoder-Fehler ("Rematrix is needed between N channels and mono"),
    # obwohl jedes Segment einzeln fehlerfrei dekodiert. Der concat-FILTER
    # oeffnet jede Datei als eigenen, unabhaengigen Input und dekodiert sauber.
    cmd = ["ffmpeg", "-y"]
    for p in segment_paths:
        cmd += ["-i", str(Path(p).resolve())]

    n = len(segment_paths)
    filter_inputs = "".join(f"[{i}:v:0][{i}:a:0]" for i in range(n))
    filter_complex = f"{filter_inputs}concat=n={n}:v=1:a=1[v][a]"

    cmd += [
        "-filter_complex", filter_complex,
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        "-fps_mode", "cfr", "-r", str(config.FPS),
        out_path,
    ]
    _run_ffmpeg(cmd)
    return out_path
