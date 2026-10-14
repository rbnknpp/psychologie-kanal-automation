"""Fragt Wachstumszahlen eines YouTube-Kanals ab (Analytics + Data API)."""
from googleapiclient.discovery import build


def _build_clients(creds):
    youtube = build("youtube", "v3", credentials=creds)
    yt_analytics = build("youtubeAnalytics", "v2", credentials=creds)
    return youtube, yt_analytics


def get_channel_overview(creds) -> dict:
    """Aktueller Kanalname + Gesamt-Abonnentenzahl."""
    youtube, _ = _build_clients(creds)
    resp = youtube.channels().list(part="snippet,statistics", mine=True).execute()
    item = resp["items"][0]
    return {
        "title": item["snippet"]["title"],
        "subscribers": int(item["statistics"]["subscriberCount"]),
    }


def get_weekly_metrics(creds, start_date: str, end_date: str) -> dict:
    """Aggregierte Kennzahlen im Zeitraum [start_date, end_date] (YYYY-MM-DD)."""
    _, yt_analytics = _build_clients(creds)
    resp = yt_analytics.reports().query(
        ids="channel==MINE",
        startDate=start_date,
        endDate=end_date,
        metrics="views,estimatedMinutesWatched,subscribersGained,subscribersLost,averageViewPercentage",
    ).execute()

    rows = resp.get("rows")
    if not rows:
        return {"views": 0, "watch_hours": 0.0, "subs_gained": 0, "subs_lost": 0, "avg_retention": 0.0}

    views, minutes, gained, lost, avg_pct = rows[0]
    return {
        "views": int(views),
        "watch_hours": round(minutes / 60, 1),
        "subs_gained": int(gained),
        "subs_lost": int(lost),
        "avg_retention": round(avg_pct, 1),
    }


def get_top_videos(creds, start_date: str, end_date: str, max_results: int = 3) -> list[dict]:
    """Die meistgesehenen Videos im Zeitraum, mit Titel und Zuschauerbindung.

    Die Zuschauerbindung (averageViewPercentage) zeigt, ob ein Video trotz
    hoher Aufrufe schlecht "haelt" (schwacher Hook) oder umgekehrt - nuetzlich,
    um Themen/Hook-Stile im Skript-Prompt gezielt nachzuschaerfen."""
    youtube, yt_analytics = _build_clients(creds)
    resp = yt_analytics.reports().query(
        ids="channel==MINE",
        startDate=start_date,
        endDate=end_date,
        metrics="views,averageViewPercentage",
        dimensions="video",
        sort="-views",
        maxResults=max_results,
    ).execute()

    rows = resp.get("rows", [])
    if not rows:
        return []

    video_ids = [r[0] for r in rows]
    stats_by_id = {r[0]: {"views": int(r[1]), "retention": round(r[2], 1)} for r in rows}

    details = youtube.videos().list(part="snippet", id=",".join(video_ids)).execute()
    titles_by_id = {v["id"]: v["snippet"]["title"] for v in details.get("items", [])}

    return [
        {"title": titles_by_id.get(vid, vid), **stats_by_id[vid]}
        for vid in video_ids
    ]


def get_ranked_videos(
    creds, start_date: str, end_date: str, order: str = "top", max_results: int = 10,
    min_views: int = 1, min_age_days: int = 0,
) -> list[dict]:
    """Wie get_top_videos, aber wahlweise die besten oder schwaechsten Videos
    ("top"/"bottom") - Grundlage fuer die Erfolgsmuster-Auswertung, die zurueck
    in die Skript-Generierung einfliesst.

    WICHTIG: Die YouTube Analytics API erlaubt fuer dieses Berichtsschema
    (dimensions=video + metrics=views,averageViewPercentage) nur absteigende
    Sortierung - "sort=views" (aufsteigend) wird mit 400 Bad Request abgelehnt.
    Fuer "bottom" wird deshalb ein groesserer Pool absteigend geholt und lokal
    von hinten gelesen.

    min_age_days filtert "bottom"-Kandidaten nach Mindestalter (Tage seit
    Veroeffentlichung) heraus: frisch hochgeladene Videos haben schlicht noch
    keine Zeit gehabt, Aufrufe zu sammeln, und wuerden sonst faelschlich als
    "schlecht angekommen" gewertet, obwohl das nur ein Zeit-, kein Qualitaetseffekt ist."""
    youtube, yt_analytics = _build_clients(creds)
    fetch_n = max_results if order == "top" else max(50, max_results * 8)
    resp = yt_analytics.reports().query(
        ids="channel==MINE",
        startDate=start_date,
        endDate=end_date,
        metrics="views,averageViewPercentage",
        dimensions="video",
        sort="-views",
        maxResults=fetch_n,
    ).execute()

    rows = [r for r in resp.get("rows", []) if int(r[1]) >= min_views]
    if not rows:
        return []
    if order == "bottom":
        rows = list(reversed(rows))

    video_ids = [r[0] for r in rows]
    stats_by_id = {r[0]: {"views": int(r[1]), "retention": round(r[2], 1)} for r in rows}

    details = youtube.videos().list(part="snippet", id=",".join(video_ids)).execute()
    titles_by_id = {v["id"]: v["snippet"]["title"] for v in details.get("items", [])}
    published_by_id = {v["id"]: v["snippet"]["publishedAt"] for v in details.get("items", [])}

    if order == "bottom" and min_age_days > 0:
        import datetime
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=min_age_days)
        video_ids = [
            vid for vid in video_ids
            if vid in published_by_id
            and datetime.datetime.fromisoformat(published_by_id[vid].replace("Z", "+00:00")) <= cutoff
        ]

    video_ids = video_ids[:max_results]

    return [
        {"video_id": vid, "title": titles_by_id.get(vid, vid), **stats_by_id[vid]}
        for vid in video_ids
    ]
