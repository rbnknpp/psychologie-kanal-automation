"""OAuth2-Anmeldung fuer YouTube Analytics (read-only), pro Kanal ein Token.

Beim ersten Aufruf pro Kanal oeffnet sich der Browser einmalig - dort den
passenden Kanal auswaehlen (siehe config.CHANNELS). Danach automatisch, kein
erneuter Login noetig.
"""
import os
import time

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

import config

SCOPES = [
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    "https://www.googleapis.com/auth/youtube.readonly",
]


def get_credentials(token_file) -> Credentials:
    if not config.CLIENT_SECRET_FILE.exists():
        raise RuntimeError(f"client_secret.json fehlt unter {config.CLIENT_SECRET_FILE}")

    creds = None
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        elif os.getenv("CI"):
            raise RuntimeError(
                f"Analytics-Token {token_file.name} ungueltig/abgelaufen - im Cloud-Lauf ist kein Login moeglich. "
                "Token am PC neu erzeugen und das passende GitHub-Secret aktualisieren."
            )
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(config.CLIENT_SECRET_FILE), SCOPES
            )
            creds = flow.run_local_server(port=0)
            # Markiert den Zeitpunkt der ECHTEN Anmeldung, unabhaengig vom
            # naechsten stillen Refresh (siehe check_oauth_age.py) - Googles
            # 7-Tage-Testmodus-Ablauf zaehlt ab hier, nicht ab dem letzten Refresh.
            marker = token_file.parent / f".oauth_granted_at_{token_file.stem}"
            marker.write_text(str(time.time()), encoding="utf-8")
        token_file.write_text(creds.to_json(), encoding="utf-8")

    return creds
