# YouTube-Kanal-Automatisierung — Psychologie & Alltags-Fakten

Automatisierte Pipeline: Claude generiert Fakten-Skripte -> edge-tts (kostenlos) spricht sie ein
-> Pexels liefert Hintergrundvideos -> ffmpeg rendert Shorts (9:16) und ein
Longform-Kompilationsvideo (16:9) mit eingebrannten Untertiteln -> optionaler
Upload zu YouTube.

## 1. Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Dann `.env` ausfuellen (siehe unten, wo man welchen Key bekommt).

### API-Keys

| Key | Wofuer | Wo bekommen |
|---|---|---|
| `ANTHROPIC_API_KEY` | Skript-/Themengenerierung | https://console.anthropic.com/settings/keys |
| `EDGE_TTS_VOICE` | Sprachausgabe (kostenlos, edge-tts) | Kein Key noetig. Stimmen auflisten: `.venv\Scripts\edge-tts --list-voices` |
| `PEXELS_API_KEY` | Kostenlose Stock-Hintergrundvideos | https://www.pexels.com/api/ (kostenloser Account reicht) |

## 2. YouTube API einrichten (einmalig, fuer den Upload)

1. https://console.cloud.google.com/ -> neues Projekt erstellen.
2. **APIs & Dienste -> Bibliothek** -> "YouTube Data API v3" suchen -> aktivieren.
3. **APIs & Dienste -> OAuth-Zustimmungsbildschirm**:
   - Nutzertyp "Extern" waehlen, App-Namen vergeben, deine E-Mail eintragen.
   - Unter "Testnutzer" dein eigenes YouTube-Google-Konto hinzufuegen (sonst
     verweigert Google spaeter den Login), solange die App im Status "Test" ist.
4. **APIs & Dienste -> Anmeldedaten -> Anmeldedaten erstellen -> OAuth-Client-ID**:
   - Anwendungstyp: **Desktop-App**.
   - Die heruntergeladene JSON-Datei umbenennen in `client_secret.json` und in
     diesen Projektordner legen (Pfad ist in `.gitignore`, wird nicht committet).
5. Beim ersten Upload-Lauf (`python upload_cli.py`) oeffnet sich automatisch der
   Browser zur Anmeldung mit dem Google-Konto, das den YouTube-Kanal besitzt.
   Danach liegt ein `token.json` hier, das automatisch erneuert wird — kein
   erneuter Login noetig.

## 3. Videos generieren

```bash
python pipeline.py
```

Erzeugt in `output/<Datum>/`:
- `shorts/01-.../short.mp4`, `shorts/02-.../short.mp4`, ... — einzelne Shorts (Standard: 3 Stueck, in `config.py` unter `SHORTS_PER_RUN` einstellbar)
- `compilation.mp4` — ein **eigenstaendiges** Longform-Video mit **anderen** Fakten
  als die Shorts (kein Recycling), mindestens `LONGFORM_MIN_DURATION_SECONDS`
  lang (Standard: 10 Minuten, wichtig fuer Mid-Roll-Werbung/Monetarisierung).
  Startet mit `LONGFORM_FACTS_START` Fakten und generiert bei Bedarf automatisch
  weitere nach, bis die Mindestlaenge erreicht ist (Obergrenze `LONGFORM_MAX_FACTS`).
- `longform/01-.../segment.mp4` — die Einzelsegmente, aus denen `compilation.mp4` besteht
- `manifest.json` — Titel/Beschreibung/Tags fuer den Upload

**Vor dem Upload reinschauen!** Automatisierte TTS/Untertitel-Sync ist meist gut,
aber stichprobenartig pruefen lohnt sich, besonders am Anfang.

## 4. Hochladen

```bash
python upload_cli.py                        # neuesten Lauf hochladen, ueber den Tag verteilt
python upload_cli.py --privacy private       # nur zur Kontrolle, nicht live stellen
python upload_cli.py --no-spread             # sofort alle oeffentlich, ohne Zeitversatz
python upload_cli.py --only shorts           # nur die Shorts, ohne Kompilation
python upload_cli.py --dir output/2026-09-15 # bestimmten Lauf hochladen
```

Standard-Sichtbarkeit ist **oeffentlich** (`config.YOUTUBE_DEFAULT_PRIVACY` in
`config.py`) — der taegliche Taskplaner-Lauf stellt Videos also ohne manuellen
Zwischenschritt live. Falls du das wieder auf Kontrolle vor Veroeffentlichung
umstellen willst, in `config.py` auf `"private"` zuruecksetzen.

**Ueber den Tag verteilte Veroeffentlichung:** Statt alle Videos eines Laufs
gleichzeitig live zu stellen, nutzt `upload_cli.py` YouTubes native geplante
Veroeffentlichung (`privacyStatus=private` + `publishAt`) mit den Uhrzeiten aus
`config.PUBLISH_TIMES` (Reihenfolge: Short 1, Short 2, ..., zuletzt die
Kompilation). Liegt eine Uhrzeit bereits in der Vergangenheit (z.B. weil der
Taskplaner-Lauf spaeter als gedacht durchlief), wird das jeweilige Video
stattdessen sofort veroeffentlicht statt geplant.

## 5. Taegliche Automatisierung (Windows Task Scheduler)

1. `run_daily.bat` liegt bereits im Projektordner (ruft `pipeline.py` und
   `upload_cli.py` nacheinander auf).
2. Windows-Taskplaner oeffnen -> "Aufgabe erstellen" -> Trigger "Taeglich" auf
   gewuenschte Uhrzeit -> Aktion: die `run_daily.bat` starten.
3. Fertig — der Kanal befuellt sich ab jetzt taeglich selbst.

## Projektstruktur

```
config.py                 Zentrale Einstellungen (Nische, Aufloesung, Anzahl Facts/Tag)
content/script_generator.py   Claude generiert Titel/Skript/Tags als JSON
tts/edge_tts_engine.py    Sprachausgabe (kostenlos) + Wort-Timestamps
video/stock_footage.py    Pexels-Hintergrundvideos suchen & cachen
video/captions.py         Timestamps -> .ass Untertitel-Datei
video/render.py           ffmpeg: Hintergrund + Audio + Untertitel -> mp4
pipeline.py                Orchestriert den kompletten Lauf
upload/                   YouTube-OAuth + Upload
upload_cli.py              CLI zum Hochladen eines Laufs
state.py                   Merkt sich verwendete Titel (keine Wiederholungen)
```

## Wichtig: Richtlinien & Risiken

- YouTube demonetarisiert zunehmend rein automatisierte "reused/generic content"
  Kanaele. Halte die Skripte inhaltlich wertig (echte, korrekte Fakten, keine
  reine Wiederholung fremder Inhalte) — das ist hier durch die Claude-Generierung
  mit Themen-Dedupe (`state.py`) bereits beruecksichtigt.
- Stock-Footage kommt lizenzfrei von Pexels (kommerzielle Nutzung erlaubt).
- Fuer Hintergrundmusik: selbst lizenzfreie Tracks besorgen (z. B. YouTube Audio
  Library) — aktuell rendert die Pipeline ohne Musik, nur Sprache.
- Bei "Made for Kids" bleibt es bewusst auf `false`, da der Content nicht an
  Kinder gerichtet ist.
- Der Kanal veroeffentlicht jetzt **vollautomatisch ohne manuelle Kontrolle**
  (`YOUTUBE_DEFAULT_PRIVACY = "public"`). Trotzdem regelmaessig ins `logs/daily_run.log`
  und in YouTube Studio schauen — die Skriptgenerierung kann inhaltlich danebenliegen
  (falsche/erfundene Fakten) oder ein Upload kann scheitern, ohne dass das sonst auffaellt.
