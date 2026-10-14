"""Schickt eine kurze Alarm-E-Mail, wenn ein taeglicher Automatik-Lauf fehlschlaegt.

Nutzt dieselben E-Mail-Zugangsdaten wie das "Kanal Berichte"-Projekt (liegt im
selben uebergeordneten Ordner), damit das App-Passwort nur an einer Stelle
gepflegt werden muss.

Aufruf: python notify.py "<Kanalname>" "<Fehlgeschlagener Schritt>"
"""
import os
import smtplib
import sys
import time
from email.mime.text import MIMEText
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent
load_dotenv(ROOT.parent / "Kanal Berichte" / ".env")

EMAIL_FROM = os.getenv("EMAIL_FROM")
EMAIL_APP_PASSWORD = os.getenv("EMAIL_APP_PASSWORD")
EMAIL_TO = os.getenv("EMAIL_TO")


def send_alert(subject: str, body: str) -> None:
    if not EMAIL_APP_PASSWORD:
        print("Kein EMAIL_APP_PASSWORD gefunden (siehe Kanal Berichte/.env) - Alarm-Mail uebersprungen.")
        return

    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO
    msg["Subject"] = subject

    # Alarm-Mails sind der letzte Rettungsanker - ein kurzer Netzwerk-/DNS-
    # Aussetzer (genau der Fall, der oft ZEITGLEICH zum eigentlichen Fehler
    # auftritt) darf die Benachrichtigung nicht stillschweigend verschlucken.
    last_error = None
    for attempt in range(1, 4):
        try:
            with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
                server.starttls()
                server.login(EMAIL_FROM, EMAIL_APP_PASSWORD)
                server.send_message(msg)
            return
        except Exception as e:
            last_error = e
            print(f"  Alarm-Mail Versuch {attempt}/3 fehlgeschlagen: {e}")
            if attempt < 3:
                time.sleep(15)

    print(f"  FEHLER: Alarm-Mail konnte nach 3 Versuchen nicht verschickt werden: {last_error}")


if __name__ == "__main__":
    channel = sys.argv[1] if len(sys.argv) > 1 else "Unbekannter Kanal"
    stage = sys.argv[2] if len(sys.argv) > 2 else "Unbekannter Schritt"

    send_alert(
        f"Automatisierung fehlgeschlagen: {channel}",
        f"Der heutige automatische Lauf ist beim Schritt '{stage}' fehlgeschlagen.\n\n"
        f"Kanal: {channel}\n"
        f"Details: siehe Protokoll des GitHub-Actions-Laufs (bzw. logs/daily_run.log am PC).\n\n"
        f"Bitte pruefen und bei Bedarf manuell nachholen.",
    )
    print("Alarm-E-Mail verschickt.")
