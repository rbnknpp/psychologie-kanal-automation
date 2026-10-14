"""Laedt die Videos eines Pipeline-Laufs zu YouTube hoch.

Ohne --privacy werden die Videos ueber den Tag verteilt veroeffentlicht
(config.PUBLISH_TIMES), solange config.YOUTUBE_DEFAULT_PRIVACY == "public" ist.

Beispiele:
  python upload_cli.py                       # neuesten Lauf hochladen, ueber den Tag verteilt
  python upload_cli.py --dir output/2026-09-15 --privacy public   # sofort oeffentlich, kein Spread
  python upload_cli.py --privacy private      # nur zur Kontrolle, nicht live stellen
  python upload_cli.py --only shorts
"""
import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from googleapiclient.discovery import build

import config
from upload.upload_video import upload
from upload.youtube_auth import get_credentials
from upload.playlists import get_or_create_playlist, add_video
from upload.comments import post_comment


def _latest_run_dir():
    runs = sorted(p for p in config.OUTPUT_DIR.iterdir() if p.is_dir())
    if not runs:
        raise SystemExit("Kein Lauf in output/ gefunden. Erst 'python pipeline.py' ausfuehren.")
    return runs[-1]


def _load_manifest(run_dir: Path) -> list:
    """Laedt manifest.json und ergaenzt Upload-Status aus uploaded_ledger.json.
    pipeline.py schreibt das Manifest ohne Upload-Flags neu, wenn sie parallel
    zu einem Upload noch laeuft - das Ledger verhindert dann Doppel-Uploads."""
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    ledger_path = run_dir / "uploaded_ledger.json"
    if ledger_path.exists():
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        for item in manifest:
            entry = ledger.get(item.get("video_path"))
            if entry:
                item.update(entry)
                item["uploaded"] = True
    return manifest


def _write_manifest(run_dir: Path, manifest: list) -> None:
    # Eintraege, die pipeline.py inzwischen parallel neu ergaenzt hat, nicht verlieren
    manifest_path = run_dir / "manifest.json"
    if manifest_path.exists():
        try:
            on_disk = json.loads(manifest_path.read_text(encoding="utf-8"))
            known = {i.get("video_path") for i in manifest}
            manifest.extend(i for i in on_disk if i.get("video_path") not in known)
        except (ValueError, OSError):
            pass
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    ledger = {}
    for item in manifest:
        if item.get("uploaded") and item.get("youtube_id"):
            ledger[item["video_path"]] = {
                k: item[k] for k in ("youtube_id", "publish_at", "comment_posted") if k in item
            }
    (run_dir / "uploaded_ledger.json").write_text(
        json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _compute_publish_at(index: int) -> str | None:
    """Naechster passender Zeitpunkt aus config.PUBLISH_TIMES (RFC3339 UTC) oder
    None, wenn kein Slot mehr uebrig oder der Slot heute schon vorbei ist."""
    if index >= len(config.PUBLISH_TIMES):
        return None

    tz = ZoneInfo(config.PUBLISH_TIMEZONE)
    now_local = datetime.now(tz)
    hh, mm = map(int, config.PUBLISH_TIMES[index].split(":"))
    target_local = now_local.replace(hour=hh, minute=mm, second=0, microsecond=0)

    if target_local <= now_local:
        return None  # Slot heute schon vorbei -> sofort veroeffentlichen statt planen

    target_utc = target_local.astimezone(ZoneInfo("UTC"))
    return target_utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_due(publish_at: str | None) -> bool:
    """True, wenn das Video inzwischen oeffentlich sein sollte (oder nie
    geplant war) - erst dann laesst YouTube das Posten von Kommentaren zu."""
    if not publish_at:
        return True
    due_time = datetime.strptime(publish_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) >= due_time


def _post_pending_comments(youtube, days_back: int = 4) -> None:
    """Holt Kommentare nach, die beim Upload noch nicht gepostet werden
    konnten, weil das Video zu dem Zeitpunkt noch als 'geplant' (privat)
    galt - YouTube erlaubt Kommentare erst, sobald das Video oeffentlich ist."""
    cutoff = datetime.now().date() - timedelta(days=days_back)

    for run_dir in sorted(config.OUTPUT_DIR.iterdir()):
        if not run_dir.is_dir():
            continue
        try:
            run_date = datetime.fromisoformat(run_dir.name).date()
        except ValueError:
            continue
        if run_date < cutoff:
            continue

        if not (run_dir / "manifest.json").exists():
            continue
        manifest = _load_manifest(run_dir)
        changed = False

        for item in manifest:
            if not item.get("uploaded") or not item.get("comment_prompt"):
                continue
            if item.get("comment_posted"):
                continue
            if not _is_due(item.get("publish_at")):
                continue
            try:
                post_comment(youtube, item["youtube_id"], item["comment_prompt"])
                item["comment_posted"] = True
                changed = True
                print(f"  Nachtraeglicher Kommentar gepostet: {item['title']}")
            except Exception as e:
                print(f"  WARNUNG: Nachtraeglicher Kommentar-Post fehlgeschlagen ({item['title']}): {e}")

        if changed:
            _write_manifest(run_dir, manifest)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", type=str, default=None, help="Pfad zum Lauf-Ordner (Default: neuester)")
    parser.add_argument("--privacy", choices=["private", "unlisted", "public"], default=None)
    parser.add_argument("--only", choices=["all", "shorts", "longform"], default="all")
    parser.add_argument("--no-spread", action="store_true", help="Zeitversetzte Veroeffentlichung deaktivieren")
    args = parser.parse_args()

    run_dir = Path(args.dir) if args.dir else _latest_run_dir()
    manifest = _load_manifest(run_dir)

    use_spread = (
        args.privacy is None
        and not args.no_spread
        and config.YOUTUBE_DEFAULT_PRIVACY == "public"
    )

    youtube = build("youtube", "v3", credentials=get_credentials())

    print("Pruefe auf nachzuholende Kommentare aelterer Uploads...")
    _post_pending_comments(youtube)

    changed = False
    for i, item in enumerate(manifest):
        if item.get("uploaded"):
            print(f"Bereits hochgeladen, ueberspringe: {item['title']}")
            continue
        if args.only == "shorts" and not item["is_short"]:
            continue
        if args.only == "longform" and item["is_short"]:
            continue

        # Das taegliche Longform-Video (>= 10 Min) geht sofort live; nur die Shorts werden gestaffelt.
        publish_at = _compute_publish_at(i) if (use_spread and item["is_short"]) else None

        print(f"Lade hoch: {item['title']}" + (f" (geplant: {publish_at})" if publish_at else ""))
        video_id = upload(
            item["video_path"],
            item["title"],
            item["description"],
            item["tags"],
            privacy_status=args.privacy,
            publish_at=publish_at,
            thumbnail_path=item.get("thumbnail_path"),
            srt_path=item.get("srt_path") if (not item["is_short"] or getattr(config, "UPLOAD_CAPTIONS_FOR_SHORTS", True)) else None,
        )
        item["uploaded"] = True
        item["youtube_id"] = video_id
        if publish_at:
            item["publish_at"] = publish_at
        changed = True
        _write_manifest(run_dir, manifest)

        try:
            playlist_name = item.get("playlist") or (config.SHORTS_PLAYLIST_NAME if item["is_short"] else config.LONGFORM_PLAYLIST_NAME)
            playlist_id = get_or_create_playlist(youtube, playlist_name)
            add_video(youtube, playlist_id, video_id)
            print(f"  In Playlist '{playlist_name}' einsortiert.")
        except Exception as e:
            print(f"  WARNUNG: Playlist-Einsortierung fehlgeschlagen: {e}")

        if item.get("comment_prompt"):
            if _is_due(publish_at):
                try:
                    post_comment(youtube, video_id, item["comment_prompt"])
                    item["comment_posted"] = True
                    print("  Diskussions-Kommentar gepostet.")
                except Exception as e:
                    print(f"  WARNUNG: Kommentar-Post fehlgeschlagen: {e}")
            else:
                print("  Kommentar zurueckgestellt (Video noch nicht oeffentlich, wird spaeter nachgeholt).")
            _write_manifest(run_dir, manifest)

    if not changed:
        print("Nichts Neues hochzuladen.")


if __name__ == "__main__":
    main()
