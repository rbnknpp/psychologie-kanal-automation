"""Sucht und laedt passende Hintergrund-Clips ueber die Pexels-API."""
import hashlib
import random
import time

import requests

import config

SEARCH_URL = "https://api.pexels.com/videos/search"


def _get(url: str, retries: int = 3, **kwargs) -> requests.Response:
    """GET mit Wiederholungen - ein einzelner Timeout darf keinen ganzen Lauf abbrechen."""
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, **kwargs)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            last_error = e
            print(f"  Pexels-Anfrage Versuch {attempt}/{retries} fehlgeschlagen: {e}")
            if attempt < retries:
                time.sleep(5 * attempt)
    raise last_error


def _pick_file(video: dict, orientation: str) -> dict | None:
    files = video.get("video_files", [])
    wanted_portrait = orientation == "portrait"
    candidates = [
        f
        for f in files
        if f.get("file_type") == "video/mp4"
        and ((f["height"] > f["width"]) == wanted_portrait)
    ]
    if not candidates:
        return None
    # bevorzugt hd Qualitaet, aber nicht unnoetig riesig
    candidates.sort(key=lambda f: abs(f.get("width", 0) - (1080 if wanted_portrait else 1920)))
    return candidates[0]


def _search(query: str, orientation: str, min_duration: float) -> list[dict]:
    if not config.PEXELS_API_KEY:
        raise RuntimeError("PEXELS_API_KEY fehlt in .env")
    r = _get(
        SEARCH_URL,
        headers={"Authorization": config.PEXELS_API_KEY},
        params={
            "query": query,
            "orientation": orientation,
            "per_page": 15,
            "min_duration": int(min_duration),
        },
        timeout=30,
    )
    return r.json().get("videos", [])


def _download(file: dict) -> str:
    cache_key = hashlib.md5(file["link"].encode()).hexdigest()[:16]
    out_path = config.STOCK_CACHE_DIR / f"{cache_key}.mp4"
    if not out_path.exists():
        data = _get(file["link"], timeout=120)
        tmp_path = out_path.with_suffix(".part")
        tmp_path.write_bytes(data.content)
        tmp_path.replace(out_path)  # nie eine halb geschriebene Datei im Cache lassen
    return str(out_path)


def fetch_clip(keywords: list[str], orientation: str = "portrait", min_duration: float = 5.0) -> str:
    """Sucht ein Stock-Video zu den Keywords, laedt es (gecacht) und gibt den lokalen Pfad zurueck."""
    return fetch_clips(keywords, orientation, count=1, min_duration=min_duration)[0]


def fetch_clips(
    keywords: list[str], orientation: str = "portrait", count: int = 1, min_duration: float = 5.0
) -> list[str]:
    """Liefert `count` verschiedene Clips (abwechselnd aus allen Keywords), damit ein
    Video nicht aus einem einzigen, endlos wiederholten Hintergrund besteht."""
    queries = list(keywords) if keywords else ["abstract background"]

    per_query: list[list[tuple[dict, dict]]] = []
    seen_ids: set[int] = set()
    for q in queries:
        found = []
        videos = _search(q, orientation, min_duration)
        random.shuffle(videos)
        for v in videos[:10]:
            f = _pick_file(v, orientation)
            if f is not None and v["id"] not in seen_ids:
                seen_ids.add(v["id"])
                found.append((v, f))
        per_query.append(found)

    picked: list[tuple[dict, dict]] = []
    while len(picked) < count and any(per_query):
        for found in per_query:
            if found and len(picked) < count:
                picked.append(found.pop(0))

    if not picked:
        for v in _search("abstract", orientation, min_duration)[:8]:
            f = _pick_file(v, orientation)
            if f is not None:
                picked.append((v, f))
                break
    if not picked:
        raise RuntimeError(f"Kein Pexels-Video gefunden fuer {queries}")

    return [_download(f) for _, f in picked]
