"""Kompletter Lauf: Themen generieren -> Sprache -> Hintergrund -> Video rendern.

Erzeugt pro Lauf ZWEI unabhaengige Inhalte (keine gemeinsamen Fakten):
  - config.SHORTS_PER_RUN Shorts (9:16, ~60s)
  - 1 Longform-Video (16:9) aus eigenen, anderen Fakten, mindestens
    config.LONGFORM_MIN_DURATION_SECONDS lang (Monetarisierungs-Schwelle)

Aufruf:  python pipeline.py
"""
import datetime
import json
import re

import config
import state
from content.script_generator import generate_facts
from tts.edge_tts_engine import synthesize
from video.captions import build_ass
from video.stock_footage import fetch_clips
from video.render import render_segment, concat_segments
from video.thumbnail import build_thumbnail, build_cover
from video.intro import get_or_build_intro
from video.srt import build_srt
from content.hypnosis_script import generate_hypnosis_script
from video.hypnosis import build_voice_track, build_still, render_hypnosis


SHORT_CLIP_SECONDS = 10      # ein Hintergrund-Clip pro ~10s Short
LONGFORM_CLIP_SECONDS = 18   # pro ~18s Longform-Segment (spart Downloads/Speicher)


def _slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:60] or "clip"


def _fmt_timestamp(seconds: float) -> str:
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _cross_link_line() -> str:
    return f"Mehr zu {config.OTHER_CHANNEL_NAME}: {config.OTHER_CHANNEL_URL}"


def _render_short(fact, item_dir):
    audio_path = item_dir / "audio.mp3"
    alignment = synthesize(fact["script"], str(audio_path))
    duration = alignment["duration"]

    ass_path = item_dir / "captions_short.ass"
    build_ass(alignment, str(ass_path), config.SHORT_RESOLUTION, config.CAPTION_FONT_SIZE_SHORT)

    bg = fetch_clips(
        fact["search_keywords"], orientation="portrait",
        count=max(1, round(duration / SHORT_CLIP_SECONDS)), min_duration=5,
    )
    cover_path = item_dir / "cover.png"
    build_cover(fact["thumbnail_text"], str(cover_path))

    out_path = item_dir / "short.mp4"
    render_segment(
        bg, str(audio_path), str(ass_path), str(out_path), config.SHORT_RESOLUTION, duration,
        hook_zoom=True, cover_path=str(cover_path),
    )

    thumb_path = item_dir / "thumbnail.jpg"
    build_thumbnail(fact["thumbnail_text"], str(thumb_path))

    srt_path = item_dir / "captions.srt"
    build_srt(alignment["words"], str(srt_path))

    return out_path, thumb_path, srt_path, duration


def _render_longform_segment(fact, item_dir):
    audio_path = item_dir / "audio.mp3"
    alignment = synthesize(fact["script"], str(audio_path))
    duration = alignment["duration"]

    ass_path = item_dir / "captions_long.ass"
    build_ass(alignment, str(ass_path), config.LONGFORM_RESOLUTION, config.CAPTION_FONT_SIZE_LONG)

    bg = fetch_clips(
        fact["search_keywords"], orientation="landscape",
        count=max(1, round(duration / LONGFORM_CLIP_SECONDS)), min_duration=5,
    )
    out_path = item_dir / "segment.mp4"
    render_segment(bg, str(audio_path), str(ass_path), str(out_path), config.LONGFORM_RESOLUTION, duration)
    return out_path, duration, alignment["words"]


def run_longform(run_dir, extra_avoid: list[str] = None, out_name: str = "compilation"):
    """Generiert ein eigenstaendiges Longform-Video (eigene Fakten) bis zur
    Mindestlaenge und schreibt compilation.mp4 + *_metadata.json in run_dir.
    Gibt (compilation_meta, longform_facts) zurueck. Ruft state.record_facts auf."""
    longform_dir = run_dir / "longform"
    longform_dir.mkdir(parents=True, exist_ok=True)

    print(f"Generiere Fakten fuer Longform-Video (Ziel: >= {config.LONGFORM_MIN_DURATION_SECONDS // 60} Min)...")
    longform_facts = []
    longform_segments = []
    chapters = [(0.0, "Intro")]
    all_words = []
    total_duration = 0.0
    intro_duration = 3.0  # muss zu video/intro.py:build_intro()'s Default passen
    cursor = intro_duration
    batch_size = config.LONGFORM_FACTS_START
    all_used_titles = list(extra_avoid or [])

    while total_duration < config.LONGFORM_MIN_DURATION_SECONDS and len(longform_facts) < config.LONGFORM_MAX_FACTS:
        remaining_budget = config.LONGFORM_MAX_FACTS - len(longform_facts)
        n = min(batch_size, remaining_budget)
        new_facts = generate_facts(n, extra_avoid=all_used_titles)
        all_used_titles += [f["title"] for f in new_facts]

        for fact in new_facts:
            idx = len(longform_facts) + 1
            item_dir = longform_dir / f"{idx:02d}-{_slugify(fact['title'])}"
            item_dir.mkdir(parents=True, exist_ok=True)
            (item_dir / "fact.json").write_text(json.dumps(fact, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  Longform-Segment ({idx}): {fact['title']} (bisher {total_duration/60:.1f} Min)")
            seg_out, duration, words = _render_longform_segment(fact, item_dir)
            longform_facts.append(fact)
            longform_segments.append(str(seg_out))
            chapters.append((cursor, fact["thumbnail_text"]))
            all_words += [{"text": w["text"], "start": w["start"] + cursor, "end": w["end"] + cursor} for w in words]
            cursor += duration
            total_duration += duration

        batch_size = 3  # bei Nachgenerieren kleinere Haeppchen

    if total_duration < config.LONGFORM_MIN_DURATION_SECONDS:
        print(f"  WARNUNG: Longform-Video erreicht nur {total_duration/60:.1f} Min "
              f"(Ziel {config.LONGFORM_MIN_DURATION_SECONDS/60:.0f} Min), LONGFORM_MAX_FACTS erreicht.")

    print("Baue Longform-Video (mit Intro)...")
    compilation_out = run_dir / f"{out_name}.mp4"
    concat_segments([get_or_build_intro()] + longform_segments, str(compilation_out))

    titles = ", ".join(f["title"] for f in longform_facts)
    hashtag = config.NICHE_LABEL.lower().replace(" ", "")
    thumb_path = run_dir / f"{out_name}_thumbnail.jpg"
    build_thumbnail(f"{len(longform_facts)} {config.NICHE_LABEL}-Fakten", str(thumb_path))

    srt_path = run_dir / f"{out_name}.srt"
    build_srt(all_words, str(srt_path))

    chapters_block = "\n".join(f"{_fmt_timestamp(t)} {label}" for t, label in chapters)

    compilation_meta = {
        "title": f"{len(longform_facts)} faszinierende {config.NICHE_LABEL}-Fakten, die du kennen solltest",
        "description": (
            f"In diesem Video: {titles}.\n\n"
            f"Kapitel:\n{chapters_block}\n\n"
            f"{_cross_link_line()}\n\n"
            f"#{hashtag} #fakten #wusstestdu"
        ),
        "tags": sorted({t for f in longform_facts for t in f["tags"]})[:500],
        "is_short": False,
        "video_path": str(compilation_out),
        "thumbnail_path": str(thumb_path),
        "srt_path": str(srt_path),
        "comment_prompt": f"Welcher der {len(longform_facts)} Fakten hat dich am meisten überrascht? Schreib's in die Kommentare! 👇",
    }
    (run_dir / f"{out_name}_metadata.json").write_text(
        json.dumps(compilation_meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    state.record_facts(longform_facts)

    print(f"Fertig. Longform-Video ({len(longform_facts)} Fakten, {total_duration/60:.1f} Min) in {compilation_out}")
    return compilation_meta, longform_facts


def run(shorts_n: int = None, skip_longform: bool = False):
    shorts_n = shorts_n or config.SHORTS_PER_RUN
    run_date = datetime.date.today().isoformat()
    run_dir = config.OUTPUT_DIR / run_date
    shorts_dir = run_dir / "shorts"
    shorts_dir.mkdir(parents=True, exist_ok=True)

    manifest = []

    # --- Shorts -------------------------------------------------------------
    print(f"[1/3] Generiere {shorts_n} Fakten fuer Shorts...")
    shorts_facts = generate_facts(shorts_n)

    for i, fact in enumerate(shorts_facts, start=1):
        item_dir = shorts_dir / f"{i:02d}-{_slugify(fact['title'])}"
        item_dir.mkdir(parents=True, exist_ok=True)
        (item_dir / "fact.json").write_text(json.dumps(fact, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[2/3] Short ({i}/{shorts_n}): {fact['title']}")
        short_out, thumb_out, srt_out, _ = _render_short(fact, item_dir)
        manifest.append({
            "title": fact["title"],
            "description": f"{fact['description']}\n\n{_cross_link_line()}\n\n#Shorts",
            "tags": fact["tags"],
            "is_short": True,
            "video_path": str(short_out),
            "thumbnail_path": str(thumb_out),
            "srt_path": str(srt_out),
            "comment_prompt": fact["comment_prompt"],
        })
        # Nach jedem Short sofort schreiben - stuerzt der Longform-Teil danach
        # ab, sind die bereits fertigen Shorts trotzdem hochladbar statt verloren.
        (run_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    state.record_facts(shorts_facts)

    if skip_longform:
        print(f"Testlauf: kein langes Video. Fertig, {shorts_n} Short(s) in {run_dir}")
        return run_dir

    # --- Langes Video: am Hypnose-Tag die Einschlaf-Hypnose, sonst Fakten-Kompilation
    if datetime.date.today().weekday() == config.HYPNOSIS_WEEKDAY:
        print("[3/3] Hypnose-Video (ersetzt heute das Longform-Video)...")
        long_meta = run_hypnosis(run_dir)
        longform_facts = []
    else:
        print("[3/3] Longform-Video...")
        long_meta, longform_facts = run_longform(
            run_dir, extra_avoid=[f["title"] for f in shorts_facts]
        )
    manifest.append(long_meta)

    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"Fertig. {shorts_n} Shorts + 1 langes Video in {run_dir}")
    return run_dir


_HYPNOSIS_THEMES_FILE = config.ROOT / "hypnosis_themes.json"


def run_hypnosis(run_dir, total_seconds: int = None, script: dict = None) -> dict:
    """Einschlaf-Hypnose: Skript (Claude) -> langsame Stimme mit Pausen -> Klangteppich
    bis zur Ziellaenge -> Standbild-Video. Schreibt hypnosis.mp4 + Metadaten in run_dir."""
    total_seconds = total_seconds or config.HYPNOSIS_TOTAL_SECONDS
    used = json.loads(_HYPNOSIS_THEMES_FILE.read_text(encoding="utf-8")) if _HYPNOSIS_THEMES_FILE.exists() else []
    script = script or generate_hypnosis_script(avoid_themes=used[-12:])
    work = run_dir / "hypnosis"
    work.mkdir(parents=True, exist_ok=True)
    (work / "script.json").write_text(json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"  Thema: {script['theme']} ({len(script['sections'])} Abschnitte)")
    voice, chapters = build_voice_track(script["sections"], work / "parts")
    lead_in = 15.0
    voice_end = lead_in + _audio_seconds(voice)
    if voice_end > total_seconds - 180:
        total_seconds = int(voice_end + 600)  # Stimme nie abschneiden, danach mind. 10 Min Klang
    still = build_still(script["thumbnail_text"], work / "still.jpg")
    out = run_dir / "hypnosis.mp4"
    print(f"  Rendere {total_seconds // 60} Min (Stimme bis {voice_end / 60:.1f} Min)...")
    render_hypnosis(voice, still, out, total_seconds, lead_in=lead_in)

    # Das ruhige Standbild ist auch das Thumbnail (passt besser als der laute Fakten-Stil)
    thumb = still
    chapter_lines = ["00:00 Ankommen"] + [
        f"{_fmt_timestamp(t + lead_in)} {label}" for t, label in chapters if t > 0
    ] + [f"{_fmt_timestamp(voice_end + 5)} Nur Klänge zum Weiterschlafen"]
    minutes = total_seconds // 60
    title = script["title"].strip()
    if "hypnose" not in title.lower():
        title = f"{title} | {minutes} Min Hypnose zum Einschlafen"
    meta = {
        "title": title[:100],
        "description": (
            f"{script['description']}\n\n"
            f"Kapitel:\n" + "\n".join(chapter_lines) + "\n\n"
            "Hinweis: Nicht beim Autofahren oder Bedienen von Maschinen anhören. "
            "Diese Entspannung ersetzt keine ärztliche oder therapeutische Behandlung. "
            "Stimme und Klänge sind KI-generiert.\n\n"
            f"{_cross_link_line()}\n\n#einschlafen #hypnose #entspannung #schlafen"
        ),
        "tags": sorted(set(script.get("tags", []) + ["einschlafen", "hypnose", "entspannung", "schlaf", "meditation"]))[:30],
        "is_short": False,
        "video_path": str(out),
        "thumbnail_path": str(thumb),
        "playlist": config.HYPNOSIS_PLAYLIST_NAME,
        "comment_prompt": "Bei welcher Stelle bist du eingeschlafen? 🌙",
    }
    (run_dir / "hypnosis_metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    _HYPNOSIS_THEMES_FILE.write_text(json.dumps(used + [script["theme"]], ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Fertig. Hypnose-Video ({minutes} Min) in {out}")
    return meta


def _audio_seconds(path) -> float:
    import subprocess
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


if __name__ == "__main__":
    import sys

    if "--test" in sys.argv:  # Cloud-Testlauf: 1 Short, kein langes Video
        run(shorts_n=1, skip_longform=True)
    else:
        run()
