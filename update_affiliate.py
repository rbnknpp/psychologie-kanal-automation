"""Stellt alte Videobeschreibungen nach und nach auf den Amazon-Partnerlink um (Robin 21.10.2026).

- Videos mit dem alten Buch-Link (ohne ?tag=) bekommen die aktuelle config.BOOK_LINE statt der alten Zeile.
- Videos ganz ohne Buch-Hinweis bekommen config.BOOK_LINE vor den Hashtags bzw. am Ende angehaengt.
- Private Videos werden uebersprungen (z. B. die am 16.10. versteckten Doppel-Uploads).
- Hoechstens --max Videos pro Lauf, damit YouTube es nicht als Massenaenderung wertet und das API-Kontingent reicht
  (videos.update = 50 Einheiten). Erledigte Video-IDs stehen in affiliate_done.json.

Aufruf: python update_affiliate.py [--max 25] [--dry-run]
"""
import argparse
import json
import re
from pathlib import Path

from googleapiclient.discovery import build

import config
from upload.youtube_auth import get_credentials

DONE_FILE = config.ROOT / "affiliate_done.json"
OLD_LINE = re.compile(r"^Das Buch zum Kanal:.*amazon\.de/dp/B0HNFH6PC4(?!\?tag=).*$", re.M)
SNIPPET_KEYS = ("title", "description", "tags", "categoryId", "defaultLanguage", "defaultAudioLanguage")


def new_description(desc: str) -> str | None:
    if f"tag={config.AMAZON_TAG}" in desc:
        return None
    if OLD_LINE.search(desc):
        return OLD_LINE.sub(lambda _m: config.BOOK_LINE, desc, count=1)
    # kein Buch-Hinweis: vor dem Hashtag-Block einfuegen, sonst ans Ende
    m = re.search(r"\n\s*(#\w[^\n]*)\s*$", desc)
    if m:
        return desc[: m.start()].rstrip() + f"\n\n{config.BOOK_LINE}\n\n" + m.group(1)
    return desc.rstrip() + f"\n\n{config.BOOK_LINE}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=25)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    done = set(json.loads(DONE_FILE.read_text(encoding="utf-8"))) if DONE_FILE.exists() else set()
    yt = build("youtube", "v3", credentials=get_credentials())
    uploads = yt.channels().list(part="contentDetails", mine=True).execute()["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]

    changed, checked, token = 0, 0, None
    while changed < a.max:
        page = yt.playlistItems().list(part="contentDetails", playlistId=uploads, maxResults=50, pageToken=token).execute()
        ids = [i["contentDetails"]["videoId"] for i in page["items"] if i["contentDetails"]["videoId"] not in done]
        if ids:
            vids = yt.videos().list(part="snippet,status", id=",".join(ids)).execute()["items"]
            for v in vids:
                if changed >= a.max:
                    break
                checked += 1
                if v["status"].get("privacyStatus") == "private":
                    continue
                desc = new_description(v["snippet"].get("description", ""))
                if desc is None:
                    done.add(v["id"])
                    continue
                if len(desc) > 5000:
                    print(f"  zu lang, uebersprungen: {v['id']}")
                    done.add(v["id"])
                    continue
                snippet = {k: v["snippet"][k] for k in SNIPPET_KEYS if k in v["snippet"]}
                snippet["description"] = desc
                if a.dry_run:
                    print(f"  [Probe] {v['id']} {snippet['title'][:50]}")
                else:
                    yt.videos().update(part="snippet", body={"id": v["id"], "snippet": snippet}).execute()
                    print(f"  umgestellt: {v['id']} {snippet['title'][:50]}")
                done.add(v["id"])
                changed += 1
        token = page.get("nextPageToken")
        if not token:
            break
    if not a.dry_run:
        DONE_FILE.write_text(json.dumps(sorted(done), indent=0), encoding="utf-8")
    print(f"Partnerlink: {changed} Videos umgestellt, {checked} geprueft, {len(done)} insgesamt erledigt.")


if __name__ == "__main__":
    main()
