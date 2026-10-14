"""Laedt ein fertiges Video ueber die YouTube Data API v3 hoch."""
import time

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

import config
from upload.youtube_auth import get_credentials


def _fit_tags(tags: list[str], max_bytes: int = 460) -> list[str]:
    """YouTube begrenzt Tags auf insgesamt ~500 Byte (kommasepariert, UTF-8),
    nicht auf 500 Stueck - haengt so viele Tags an, wie reinpassen (mit
    Sicherheitsmarge, da Umlaute in UTF-8 mehrere Byte belegen)."""
    fitted = []
    used = 0
    for t in tags:
        piece = ("," + t) if fitted else t
        added = len(piece.encode("utf-8"))
        if used + added > max_bytes:
            break
        fitted.append(t)
        used += added
    return fitted


def upload(
    video_path: str,
    title: str,
    description: str,
    tags: list[str],
    privacy_status: str = None,
    publish_at: str = None,
    thumbnail_path: str = None,
    srt_path: str = None,
) -> str:
    """Laedt das Video hoch und gibt die YouTube-Video-ID zurueck.

    Wenn `publish_at` gesetzt ist (RFC3339-UTC-Zeitstempel, z.B. "2026-09-17T07:00:00Z"),
    wird das Video als "private" mit geplanter Veroeffentlichung hochgeladen - YouTube
    schaltet es dann selbststaendig zur angegebenen Zeit frei.
    """
    creds = get_credentials()
    youtube = build("youtube", "v3", credentials=creds)

    status = {"selfDeclaredMadeForKids": False}
    if publish_at:
        status["privacyStatus"] = "private"
        status["publishAt"] = publish_at
    else:
        status["privacyStatus"] = privacy_status or config.YOUTUBE_DEFAULT_PRIVACY

    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": _fit_tags(tags),
            "categoryId": config.YOUTUBE_CATEGORY_ID,
        },
        "status": status,
    }

    media = MediaFileUpload(video_path, chunksize=-1, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        upload_status, response = request.next_chunk()
        if upload_status:
            print(f"  Upload {int(upload_status.progress() * 100)}%")

    video_id = response["id"]
    if publish_at:
        print(f"  Hochgeladen: https://youtu.be/{video_id} (geplant fuer {publish_at})")
    else:
        print(f"  Hochgeladen: https://youtu.be/{video_id} (Status: {status['privacyStatus']})")

    # Thumbnail/Untertitel sind "nice to have" auf einem bereits erfolgreich
    # hochgeladenen Video - ein Fehler hier darf nicht dazu fuehren, dass
    # video_id verloren geht und das Video beim naechsten Lauf doppelt
    # hochgeladen wird. Deshalb einzeln abgesichert statt das Video zu blocken.
    if thumbnail_path:
        try:
            youtube.thumbnails().set(
                videoId=video_id, media_body=MediaFileUpload(thumbnail_path, mimetype="image/jpeg")
            ).execute()
            print("  Thumbnail gesetzt.")
        except Exception as e:
            print(f"  WARNUNG: Thumbnail-Upload fehlgeschlagen: {e}")

    if srt_path:
        # YouTube braucht nach dem Video-Insert manchmal ein paar Sekunden, bis
        # es Untertitel-Uploads fuer die neue Video-ID akzeptiert (sonst 403
        # "insufficient permissions") - daher mit kurzem Backoff retrien.
        caption_attempts = 4
        for attempt in range(1, caption_attempts + 1):
            try:
                youtube.captions().insert(
                    part="snippet",
                    body={
                        "snippet": {
                            "videoId": video_id,
                            "language": "de",
                            "name": "Deutsch",
                            "isDraft": False,
                        }
                    },
                    media_body=MediaFileUpload(srt_path, mimetype="text/plain"),
                ).execute()
                print("  Untertitel hochgeladen.")
                break
            except Exception as e:
                if attempt < caption_attempts:
                    print(f"  Untertitel-Upload Versuch {attempt}/{caption_attempts} fehlgeschlagen, warte kurz: {e}")
                    time.sleep(10)
                else:
                    print(f"  WARNUNG: Untertitel-Upload endgueltig fehlgeschlagen: {e}")

    return video_id
