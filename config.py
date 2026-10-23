"""Zentrale Konfiguration fuer den Kanal und die Pipeline."""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).parent
OUTPUT_DIR = ROOT / "output"
ASSETS_DIR = ROOT / "assets"
STOCK_CACHE_DIR = ASSETS_DIR / "stock_cache"
STATE_FILE = ROOT / "state.json"

for d in (OUTPUT_DIR, ASSETS_DIR, STOCK_CACHE_DIR):
    d.mkdir(parents=True, exist_ok=True)

# --- Nische / Kanal-Persona -------------------------------------------------
NICHE_DESCRIPTION = (
    "Ein Psychologie- und Alltags-Fakten-Kanal. Kurze, ueberraschende, "
    "wissenschaftlich fundierte Fakten ueber menschliches Verhalten, das Gehirn, "
    "Gewohnheiten und Beziehungen. Ton: neugierig, klar, leicht dramatisch am Hook, "
    "niemals reisserisch oder falsch. Keine medizinischen Ratschlaege, keine Diagnosen."
)
CHANNEL_LANGUAGE = "de"  # Sprache der Skripte ("de" oder "en")
NICHE_LABEL = "Psychologie"  # Kurzes Label fuer generierte Titel/Hashtags (z.B. "Finanz", "Psychologie")

# --- API / Modelle -----------------------------------------------------------
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")

EDGE_TTS_VOICE = os.getenv("EDGE_TTS_VOICE", "de-DE-KillianNeural")  # kostenlos, kein API-Key
# "piper" = kostenlose Offline-Stimme (Cloud-Lauf), "edge" = Microsofts Edge-TTS (PC)
TTS_ENGINE = os.getenv("TTS_ENGINE", "edge")

PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")

# --- Video-Einstellungen ------------------------------------------------------
SHORT_RESOLUTION = (1080, 1920)   # 9:16
LONGFORM_RESOLUTION = (1920, 1080)  # 16:9
FPS = 30

CAPTION_FONT = "Arial"
# drawtext braucht eine Datei; unter Windows Doppelpunkt fuer die ffmpeg-Filtersyntax escapen.
# Auf Linux (GitHub Actions) liefert das Paket fonts-liberation eine Arial-Entsprechung.
DRAWTEXT_FONT_FILE = (
    "C\\:/Windows/Fonts/arialbd.ttf" if os.name == "nt"
    else "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
)
CAPTION_FONT_SIZE_SHORT = 90
CAPTION_FONT_SIZE_LONG = 54
WORDS_PER_CAPTION_CHUNK = 3

# Marken-Intro (nur Longform, nicht bei Shorts - schadet dort der Retention)
INTRO_CHANNEL_NAME = "Psychologie und Alltags Fakten"
INTRO_COLORS = ("0a0a2a", "2d1b4e")  # dunkles Navy -> tiefes Violett

# Einheitliche Thumbnails (Farbverlauf + Kernaussage)
THUMBNAIL_COLORS = ("0a0a2a", "2d1b4e")
THUMBNAIL_ACCENT_COLOR = "#b98cff"
THUMBNAIL_EMOJI = "🧠"

# Buch zum Kanal (Robin 20.10.2026), steht in jeder Beschreibung. Seit 21.10.2026 als Amazon-Partnerlink
# (PartnerNet-ID robinknapp-21) mit dem vorgeschriebenen Partner-Hinweis.
AMAZON_TAG = "robinknapp-21"
BOOK_LINE = (
    "Das Buch zum Kanal: Alltagspsychologie – 30 Aha-Momente zum Mitmachen: "
    f"https://www.amazon.de/dp/B0HNFH6PC4?tag={AMAZON_TAG}\n"
    "(Werbung: Als Amazon-Partner verdiene ich an qualifizierten Verkäufen.)"
)

# Anzahl Shorts pro Tag (Robin 20.10.2026: Menge bleibt hoch, um Aufrufe/Wiedergabezeit schnell zu sammeln)
SHORTS_PER_RUN = 10

# Longform-Kompilation: bekommt EIGENE, andere Fakten als die Shorts (kein
# Wiederverwerten). Start-Anzahl an Fakten, danach werden bei Bedarf weitere
# nachgeneriert, bis LONGFORM_MIN_DURATION_SECONDS erreicht ist (wichtig fuer
# die YouTube-Monetarisierung: Mid-Roll-Werbung braucht Videos ab 8 Minuten).
LONGFORM_FACTS_START = 10
LONGFORM_MIN_DURATION_SECONDS = 600  # 10 Minuten
LONGFORM_MAX_FACTS = 22  # Sicherheitsgrenze gegen Endlos-Nachgenerieren

# YouTube-Upload
YOUTUBE_CATEGORY_ID = "27"  # Education
YOUTUBE_DEFAULT_PRIVACY = os.getenv("YOUTUBE_DEFAULT_PRIVACY", "public")  # "private" | "unlisted" | "public"
YOUTUBE_CLIENT_SECRET_FILE = ROOT / "client_secret.json"
YOUTUBE_TOKEN_FILE = ROOT / "token.json"

# Ueber den Tag verteilte Veroeffentlichungszeiten (lokale Zeit), eine pro
# Manifest-Eintrag in Reihenfolge (Short 1, Short 2, ..., zuletzt die
# Longform-Kompilation). Muss SHORTS_PER_RUN + 1 Eintraege haben.
# Liegt eine Zeit in der Vergangenheit (z.B. Taskplaner lief spaeter als
# gedacht), wird das jeweilige Video stattdessen sofort veroeffentlicht.
PUBLISH_TIMEZONE = "Europe/Berlin"
# Der Lauf startet um ~07:15 und braucht mit 10 Shorts gut 1,5 Stunden, daher erst ab 10:00.
PUBLISH_TIMES = ["10:00", "11:15", "12:30", "13:45", "15:00", "16:15", "17:30", "18:45", "20:00", "21:15", "21:45"]

# Untertitel-Upload kostet 400 Kontingent-Einheiten pro Video. Bei Shorts sind die
# Untertitel ohnehin eingebrannt, daher dort weglassen - sonst reicht das Tageskontingent
# (10.000, geteilt mit dem Finanz-Kanal im selben Google-Projekt) nicht fuer 10 Shorts.
UPLOAD_CAPTIONS_FOR_SHORTS = False

# Entspannungs-/Einschlafvideo: kommt an diesen Tagen ZUSAETZLICH zum taeglichen langen Video
# (Robin 20.10.2026: mind. 1-2 Std, beruhigende Musik mit Melodie + sanfte Stimme)
HYPNOSIS_WEEKDAYS = [0, 1, 2, 3, 4, 5, 6]  # taeglich (Robin 23.10.2026: Einschlafvideos laufen am besten)
HYPNOSIS_TOTAL_SECONDS = 2 * 60 * 60   # Videolaenge; nach der Stimme laeuft die Musik weiter
HYPNOSIS_SCRIPT_WORDS = 3600       # ca. 35-45 Min gesprochener Text inkl. Pausen
HYPNOSIS_PUBLISH_TIME = "20:30"    # abends, wenn die Leute schlafen gehen

# Langes Video (Robin 23.10.2026): "regen" = 3 Std Regengeraeusche + Musik statt Themen-Langvideo
# ("themen" schaltet zurueck auf run_longform).
LONGFORM_MODE = "regen"
RAIN_TOTAL_SECONDS = 3 * 60 * 60
RAIN_PUBLISH_TIME = "21:30"
RAIN_PLAYLIST_NAME = "Regen & Schlafmusik"
HYPNOSIS_VOICE = os.getenv("HYPNOSIS_VOICE", "de-DE-KatjaNeural")
HYPNOSIS_RATE = "-25%"
HYPNOSIS_PITCH = "-6Hz"
HYPNOSIS_PLAYLIST_NAME = "Hypnose & Einschlafen"

# Cross-Linking zum Schwesterkanal (kostenlose gegenseitige Zielgruppen-Befruchtung)
OTHER_CHANNEL_NAME = "Finanzwissen Alltag"
OTHER_CHANNEL_URL = "https://youtube.com/channel/UCDIAMMOKwhb-LMf80YwkLtg"

# Playlist-Namen, in die neue Videos automatisch einsortiert werden
SHORTS_PLAYLIST_NAME = "Shorts"
LONGFORM_PLAYLIST_NAME = "Videos"

# Lokale Lauf-Ordner (output/<Datum>/) werden nach so vielen Tagen geloescht -
# aber nur, wenn zu diesem Zeitpunkt alle Videos darin bereits hochgeladen sind
OUTPUT_RETENTION_DAYS = 14
