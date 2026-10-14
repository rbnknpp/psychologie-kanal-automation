"""Zentrale Konfiguration fuer die woechentlichen Kanal-Berichte."""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).parent
REPORTS_DIR = ROOT / "reports"
REPORTS_DIR.mkdir(exist_ok=True)

CLIENT_SECRET_FILE = ROOT / "client_secret.json"

# Ein Kanal pro Eintrag: Name fuer den Bericht + eigene Token-Datei
# (jeder Kanal braucht eine eigene, einmalige Anmeldung - siehe auth.py)
CHANNELS = [
    {
        "label": "Psychologie und Alltags Fakten",
        "token_file": ROOT / "token_psychologie.json",
        "pipeline_dir": ROOT.parent,                       # Repo-Wurzel (Psychologie-Pipeline)
        "insights_file": ROOT.parent / "performance_insights.json",
    },
    {
        "label": "Finanzwissen Alltag",
        "token_file": ROOT / "token_finanz.json",
        "pipeline_dir": ROOT.parent / "finanz",            # (Finanz-Pipeline liegt noch nicht in der Cloud)
        "insights_file": ROOT.parent / "finanz_performance_insights.json",
    },
]

# E-Mail-Versand
EMAIL_FROM = os.getenv("EMAIL_FROM")
EMAIL_APP_PASSWORD = os.getenv("EMAIL_APP_PASSWORD")
EMAIL_TO = os.getenv("EMAIL_TO")
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
