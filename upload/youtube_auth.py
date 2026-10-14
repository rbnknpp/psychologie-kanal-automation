"""OAuth2-Anmeldung fuer die YouTube Data API v3.

Beim ersten Aufruf oeffnet sich der Browser einmalig zur Anmeldung mit dem
YouTube-Kanal-Google-Konto. Danach wird das Token lokal in token.json gespeichert
und automatisch erneuert - kein erneuter Login noetig.
"""
import os
import time

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

import config

SCOPES = [
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.force-ssl",  # noetig fuer captions().insert()
]


def get_credentials() -> Credentials:
    if not config.YOUTUBE_CLIENT_SECRET_FILE.exists():
        raise RuntimeError(
            f"client_secret.json fehlt unter {config.YOUTUBE_CLIENT_SECRET_FILE}.\n"
            "Siehe README.md, Abschnitt 'YouTube API einrichten'."
        )

    creds = None
    if config.YOUTUBE_TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(config.YOUTUBE_TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            # WICHTIG: token.json's Aenderungsdatum wird hierdurch angefasst, sagt
            # also NICHTS ueber Googles 7-Tage-Testmodus-Ablauf aus (der startet ab
            # der letzten echten interaktiven Anmeldung, nicht ab dem letzten
            # stillen Refresh). Fuer die Ablauf-Erinnerung siehe .oauth_granted_at
            # unten, die NUR beim vollen Login-Flow aktualisiert wird.
        elif os.getenv("CI"):
            raise RuntimeError(
                "YouTube-Token ungueltig oder abgelaufen - im Cloud-Lauf ist kein interaktiver Login moeglich. "
                "Token am PC neu erzeugen und das GitHub-Secret YOUTUBE_TOKEN aktualisieren."
            )
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(config.YOUTUBE_CLIENT_SECRET_FILE), SCOPES
            )
            creds = flow.run_local_server(port=0)
            (config.YOUTUBE_TOKEN_FILE.parent / ".oauth_granted_at").write_text(
                str(time.time()), encoding="utf-8"
            )
        config.YOUTUBE_TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")

    return creds
