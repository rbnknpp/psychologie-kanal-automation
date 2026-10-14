"""Postet einen ersten Diskussions-Kommentar unter ein frisch hochgeladenes Video
(Engagement-Booster). Echtes Anpinnen geht nur manuell in YouTube Studio."""


def post_comment(youtube, video_id: str, text: str) -> None:
    youtube.commentThreads().insert(
        part="snippet",
        body={
            "snippet": {
                "videoId": video_id,
                "topLevelComment": {"snippet": {"textOriginal": text}},
            }
        },
    ).execute()
