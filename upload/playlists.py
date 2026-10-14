"""Verwaltet YouTube-Playlists: legt sie bei Bedarf einmalig an (gecacht) und
sortiert hochgeladene Videos automatisch ein (mehr Sitzungsdauer -> Algorithmus)."""
import json

import config

_CACHE_FILE = config.ROOT / "playlists_cache.json"


def _load_cache() -> dict:
    if _CACHE_FILE.exists():
        return json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
    return {}


def _save_cache(cache: dict) -> None:
    _CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def get_or_create_playlist(youtube, title: str) -> str:
    cache = _load_cache()
    if title in cache:
        return cache[title]

    resp = youtube.playlists().list(part="snippet", mine=True, maxResults=50).execute()
    for item in resp.get("items", []):
        if item["snippet"]["title"] == title:
            cache[title] = item["id"]
            _save_cache(cache)
            return item["id"]

    resp = youtube.playlists().insert(
        part="snippet,status",
        body={
            "snippet": {"title": title, "description": f"Automatisch befuellte {title}-Playlist"},
            "status": {"privacyStatus": "public"},
        },
    ).execute()
    playlist_id = resp["id"]
    cache[title] = playlist_id
    _save_cache(cache)
    return playlist_id


def add_video(youtube, playlist_id: str, video_id: str) -> None:
    youtube.playlistItems().insert(
        part="snippet",
        body={
            "snippet": {
                "playlistId": playlist_id,
                "resourceId": {"kind": "youtube#video", "videoId": video_id},
            }
        },
    ).execute()
