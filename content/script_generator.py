"""Generiert Fakten-Skripte fuer Shorts via Claude API (Tool-Use fuer garantiert valides JSON)."""
import json
import re

import anthropic

import config
import state

_INSIGHTS_FILE = config.ROOT / "performance_insights.json"


def _load_performance_digest() -> str:
    """Erfolgs-/Flop-Muster der letzten Wochen (siehe Kanal Berichte/performance_insights.py).
    Datei existiert erst nach dem ersten woechentlichen Bericht - bis dahin leer."""
    if not _INSIGHTS_FILE.exists():
        return ""
    try:
        data = json.loads(_INSIGHTS_FILE.read_text(encoding="utf-8"))
        return data.get("digest_text", "")
    except (json.JSONDecodeError, OSError):
        return ""


def _performance_block() -> str:
    digest = _load_performance_digest()
    if not digest:
        return ""
    return (
        f"\nZusaetzliche Erkenntnisse aus den echten Zuschauerzahlen der letzten Tage:\n{digest}\n"
        "So nutzt du das: Uebernimm das THEMENFELD und den Aufbau erfolgreicher Videos, aber waehle "
        "jeweils eine NEUE konkrete Alltagssituation mit einer ANDEREN Kernaussage. Eine Variante eines "
        "Erfolgsvideos mit anderen Worten ist verboten - YouTube wertet gleichfoermige Videos ab.\n"
    )


_SYSTEM_PROMPT = """Du bist Autor fuer einen YouTube-Kanal mit folgender Ausrichtung:
{niche}
{performance_block}

Rufe das Tool "submit_facts" GENAU EINMAL auf, mit allen angeforderten Fakten darin.

Pro Fakt:
- "title": YouTube-Titel, max. 90 Zeichen, beschreibt eine konkrete Alltagssituation des
  Zuschauers in der Du-Form (z.B. "Warum du beim Luegen die Haende versteckst",
  "Dein Gehirn erkennt falsches Laecheln sofort"). Keine abstrakten Fakten ohne Alltagsbezug,
  kein Clickbait der nicht eingeloest wird
- "thumbnail_text": SEHR kurze knackige Kernaussage fuers Titelbild, 2-5 Woerter,
  komplett anders formuliert als der Titel (keine Wiederholung), wirkt wie ein Schlagwort
  (z.B. "DEIN GEHIRN LUEGT", "DER 5-EURO-FEHLER")
- "hook": der allererste gesprochene Satz (max 9 Woerter), muss in der ersten Sekunde fesseln: direkt mit der ueberraschenden Aussage oder einer Du-Situation starten, keine Einleitung wie "Wusstest du" oder "Heute"
- "script": der komplette gesprochene Text (inkl. hook am Anfang), 170-205 Woerter (Ziel: 60-75 Sekunden - kuerzere Shorts laufen auf dem Kanal deutlich schlechter),
  natuerliche gesprochene Sprache, kurze Saetze, endet mit einem einpraegsamen Schlusssatz,
  der inhaltlich wieder zum Hook passt, sodass das Video beim Wiederholen nahtlos weiterlaeuft
  (kein "abonniere" o.ae. - das wird separat hinzugefuegt). Ein einziger Absatz, keine
  Zeilenumbrueche.
- "description": YouTube-Beschreibung, 2-3 Saetze + 4-6 relevante Hashtags
- "comment_prompt": Kurze, lockere Diskussionsfrage zum Fakt fuers erste Kommentar
  (max. 15 Woerter), soll zum Antworten einladen (z.B. "Geht dir das auch so? Schreib's
  in die Kommentare!" oder eine konkrete Frage zum Thema)
- "tags": 8-12 YouTube-Such-Tags (einzelne Woerter oder kurze Phrasen)
- "search_keywords": 5 kurze ENGLISCHE Stichworte fuer die Suche nach passendem
  Stock-Hintergrundvideo, jedes eine ANDERE konkrete Szene passend zum Skript
  (z.B. "human brain", "person thinking", "city night walk", "hands typing phone")

Abwechslung ist Pflicht - die Videos duerfen NICHT wie aus einer Schablone wirken:
- Themen-Schwerpunkt (Robin 20.10.2026, aus den Kanal-Zahlen): mindestens 60 % der Fakten aus
  Koerpersprache, Luegen erkennen, Beruehrung, Mimik/Laecheln, Stimme und soziale Situationen
  (erste Eindruecke, Fremde, Gruppen, Naehe). Diese Themen laufen auf dem Kanal mit Abstand am
  besten. Der Rest gemischt aus Alltagspsychologie.
- Variiere die Titel-Formulierung: hoechstens die Haelfte der Titel darf mit "Warum du..."
  beginnen. Nutze auch Fragen, Zahlen ("3 Sekunden, die..."),
  Behauptungen ("Dein Gehirn glaubt...") oder kurze Aussagen.
- Variiere den Aufbau der Skripte innerhalb eines Batches: z.B. Mythos vs. Fakt,
  Alltagsszene -> Erklaerung, Kennst-du-das?-Einstieg, ueberraschende Zahl zuerst,
  kleines Mini-Experiment zum Mitmachen, Gegenfrage am Ende. Nie zweimal derselbe Aufbau.
- Erklaere den "Warum"-Mechanismus konkret und verstaendlich (eigene Einordnung statt
  blosser Aufzaehlung), nenne Studien oder Namen nur, wenn du dir absolut sicher bist.

Fakten muessen wissenschaftlich plausibel und nicht erfunden sein. Keine medizinischen
Ratschlaege oder Diagnosen. Sprache der Skripte: {language}.
"""

_TOOL = {
    "name": "submit_facts",
    "description": "Reicht die generierten Fakten-Skripte ein.",
    "input_schema": {
        "type": "object",
        "properties": {
            "facts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "thumbnail_text": {"type": "string"},
                        "hook": {"type": "string"},
                        "script": {"type": "string"},
                        "description": {"type": "string"},
                        "comment_prompt": {"type": "string"},
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "search_keywords": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": [
                        "title", "thumbnail_text", "hook", "script", "description",
                        "comment_prompt", "tags", "search_keywords",
                    ],
                },
            }
        },
        "required": ["facts"],
    },
}


_REQUIRED_FIELDS = {
    "title", "thumbnail_text", "hook", "script", "description",
    "comment_prompt", "tags", "search_keywords",
}


def _request_facts(client, n, avoid_block, theme=None):
    # Grosszuegiges Token-Budget pro Fakt (Skript + Meta-Felder + JSON-Overhead),
    # damit die Tool-Antwort bei groesseren Batches (Longform) nicht abgeschnitten wird.
    max_tokens = min(64000, 1500 + n * 1200)

    resp = client.messages.create(
        model=config.ANTHROPIC_MODEL,
        max_tokens=max_tokens,
        system=_SYSTEM_PROMPT.format(
            niche=config.NICHE_DESCRIPTION,
            language=config.CHANNEL_LANGUAGE,
            performance_block=_performance_block(),
        ),
        tools=[_TOOL],
        tool_choice={"type": "tool", "name": "submit_facts"},
        messages=[{"role": "user", "content": f"Erzeuge {n} neue, unterschiedliche Fakten.\n\n{_theme_block(theme)}{avoid_block}"}],
    )

    if resp.stop_reason == "max_tokens":
        raise ValueError(f"Antwort bei max_tokens={max_tokens} abgeschnitten (stop_reason=max_tokens)")

    tool_call = next((b for b in resp.content if b.type == "tool_use"), None)
    if tool_call is None:
        raise ValueError("Keine Tool-Antwort im Response enthalten")

    facts = tool_call.input.get("facts")
    if not isinstance(facts, list) or len(facts) != n:
        got = len(facts) if hasattr(facts, "__len__") else type(facts).__name__
        raise ValueError(f"Unerwartetes Format: {n} Fakten erwartet, erhalten: {got}")

    for f in facts:
        if not isinstance(f, dict) or not _REQUIRED_FIELDS.issubset(f.keys()):
            raise ValueError(f"Fakt fehlt Pflichtfelder: {f}")

    return facts


def _theme_block(theme):
    """Langes Video: alle Fakten zu EINEM Oberthema (Robin 20.10.2026 - keine Sammelvideos mehr,
    die wie aus der Schablone wirken; siehe YouTube-Richtlinie zu "inauthentic content")."""
    if not theme:
        return ""
    return (
        f"Alle Fakten gehoeren zu EINEM langen Video mit dem Oberthema: {theme}\n"
        "Jeder Fakt beleuchtet einen ANDEREN Aspekt dieses Themas (keine Ueberschneidungen), sie bauen "
        "logisch aufeinander auf (vom Bekannten zum Ueberraschenden). Der Hook ist hier ein Kapiteleinstieg, "
        "kein Clickbait. Die Themen-Schwerpunkt-Regel fuer Shorts gilt hier nicht.\n\n"
    )


def generate_facts(n: int, max_attempts: int = 3, extra_avoid: list[str] = None, theme: str = None) -> list[dict]:
    """Fragt Claude nach n neuen, bisher ungenutzten Fakten-Skripten (mit Retry).

    `extra_avoid` sind zusaetzliche Titel (z.B. die in diesem Lauf bereits fuer
    die Shorts generierten Fakten), die NICHT nochmal auftauchen sollen -
    unabhaengig vom gespeicherten state.json-Verlauf.
    """
    if not config.ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY fehlt in .env")

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    history = state.recent_titles(200) + list(extra_avoid or [])
    avoid = state.recent_titles(120) + list(extra_avoid or [])

    def avoid_block(extra: list[str]) -> str:
        titles = avoid + extra
        if not titles:
            return "Es gibt noch keine bisherigen Themen."
        return ("Vermeide diese bereits verwendeten Themen/Titel (keine Wiederholung, auch nicht umformuliert):\n"
                + "\n".join(f"- {t}" for t in titles))

    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            facts = _request_facts(client, n, avoid_block([]), theme)
            break
        except ValueError as e:
            last_error = e
            print(f"  Versuch {attempt}/{max_attempts} fehlgeschlagen: {e}")
    else:
        raise RuntimeError(f"Skriptgenerierung nach {max_attempts} Versuchen fehlgeschlagen: {last_error}")

    if theme:  # Kapitel eines langen Videos duerfen sich thematisch aehneln
        return facts
    # Fast gleiche Titel (z. B. dreimal "Fremder wirkt vertraut", 19.-21.10.) durch neue ersetzen
    for _ in range(2):
        dupes = _near_duplicates(facts, history)
        if not dupes:
            break
        print(f"  {len(dupes)} zu aehnliche(r) Titel, wird ersetzt: {[facts[i]['title'] for i in dupes]}")
        try:
            fresh = _request_facts(client, len(dupes), avoid_block([f["title"] for f in facts]), theme)
        except ValueError as e:
            print(f"  Ersatz fehlgeschlagen, behalte die Titel: {e}")
            break
        for i, f in zip(dupes, fresh):
            facts[i] = f
    return facts


_STOPWORDS = {"warum", "dein", "deine", "deinem", "deinen", "deiner", "dich", "dir", "du", "der", "die", "das",
              "den", "dem", "des", "ein", "eine", "einem", "einen", "einer", "und", "oder", "aber", "als", "wenn",
              "beim", "bei", "mit", "von", "vor", "nach", "fuer", "für", "ist", "sind", "wird", "kann", "mehr",
              "oefter", "öfter", "sofort", "schneller", "gehirn", "denkst", "verraet", "verrät", "was", "wie",
              "so", "nur", "auch", "nicht", "sich", "sie", "es", "auf", "aus", "zu", "im", "in", "an", "am"}


def _title_words(title: str) -> set[str]:
    words = re.findall(r"[a-zäöüß]+", title.lower())
    return {w[:6] for w in words if len(w) > 3 and w not in _STOPWORDS}


def _too_similar(a: set[str], b: set[str]) -> bool:
    if not a or not b:
        return False
    common = len(a & b)
    return common >= 2 and common / min(len(a), len(b)) >= 0.6


def _near_duplicates(facts: list[dict], history: list[str]) -> list[int]:
    """Indizes der Fakten, deren Titel einem frueheren oder einem anderen Titel im Batch zu sehr aehnelt
    (gleiche Kernwoerter, z. B. "fremd" + "vertraut" + "laecheln")."""
    past = [_title_words(t) for t in history]
    dupes, seen = [], []
    for i, f in enumerate(facts):
        words = _title_words(f["title"])
        if any(_too_similar(words, p) for p in past + seen):
            dupes.append(i)
        else:
            seen.append(words)
    return dupes


_THEME_TOOL = {
    "name": "submit_theme",
    "description": "Reicht das Oberthema fuer das heutige lange Video ein.",
    "input_schema": {
        "type": "object",
        "properties": {
            "theme": {"type": "string"},
            "title": {"type": "string"},
            "thumbnail_text": {"type": "string"},
            "description": {"type": "string"},
            "comment_prompt": {"type": "string"},
        },
        "required": ["theme", "title", "thumbnail_text", "description", "comment_prompt"],
    },
}


def generate_longform_theme(avoid: list[str] = None) -> dict:
    """Waehlt EIN Oberthema fuer das lange Video (10-13 Min) mit eigenem Titel und Thumbnail-Text.
    Robin 20.10.2026: jedes lange Video ist ein eigenstaendiges Thema statt "13 faszinierende Fakten"."""
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    avoid_txt = "\n".join(f"- {t}" for t in (avoid or [])) or "- (noch keine)"
    system = f"""Du planst das heutige lange Video (10-13 Minuten) fuer diesen YouTube-Kanal:
{config.NICHE_DESCRIPTION}
{_performance_block()}
Rufe das Tool "submit_theme" GENAU EINMAL auf mit:
- "theme": das Oberthema in einem Satz, konkret und alltagsnah (z. B. "Luegen erkennen: welche Signale
  wirklich etwas verraten und welche Mythen sind", "Warum wir Fremden vertrauen oder misstrauen",
  "Die Psychologie des Geldausgebens"). Bevorzugt Koerpersprache/Mimik/Beruehrung, soziale Situationen
  und Geld-Psychologie - diese Themen laufen auf dem Kanal am besten bzw. haben die hoechsten Werbepreise.
- "title": YouTube-Titel, max. 80 Zeichen, eigenstaendig und suchbar, KEINE Zahl der Fakten, keine
  Schablonen wie "faszinierende Fakten".
- "thumbnail_text": 2-5 Woerter, anders formuliert als der Titel.
- "description": 2-3 Saetze, worum es geht und was man danach kann, + 4-6 Hashtags.
- "comment_prompt": eine konkrete Frage zum Thema fuers erste Kommentar (max. 15 Woerter).
Nichts erfinden, keine medizinischen Ratschlaege. Sprache: {config.CHANNEL_LANGUAGE}.
Diese Oberthemen gab es schon (nicht wiederholen, auch nicht umformuliert):
{avoid_txt}"""
    for attempt in range(1, 4):
        resp = client.messages.create(
            model=config.ANTHROPIC_MODEL, max_tokens=1500, system=system,
            tools=[_THEME_TOOL], tool_choice={"type": "tool", "name": "submit_theme"},
            messages=[{"role": "user", "content": "Plane das heutige lange Video."}],
        )
        call = next((b for b in resp.content if b.type == "tool_use"), None)
        if call and all(call.input.get(k) for k in _THEME_TOOL["input_schema"]["required"]):
            return dict(call.input)
        print(f"  Oberthema Versuch {attempt}/3 unvollstaendig")
    raise RuntimeError("Kein Oberthema fuer das lange Video erhalten")


_TRAILER_TOOL = {
    "name": "submit_trailer",
    "description": "Reicht das generierte Kanal-Trailer-Skript ein.",
    "input_schema": {
        "type": "object",
        "properties": {
            "script": {"type": "string"},
            "title": {"type": "string"},
            "description": {"type": "string"},
        },
        "required": ["script", "title", "description"],
    },
}


def generate_trailer_script() -> dict:
    """Erzeugt ein kurzes Kanal-Trailer-Skript (~20-25s), das neuen Besuchern
    zeigt, was sie auf dem Kanal erwartet, und zum Abonnieren einlaedt."""
    if not config.ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY fehlt in .env")

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    system = f"""Du bist Autor fuer einen YouTube-Kanal mit folgender Ausrichtung:
{config.NICHE_DESCRIPTION}

Rufe das Tool "submit_trailer" GENAU EINMAL auf mit:
- "script": Kanal-Trailer-Text, 60-80 Woerter (~20-25s gesprochen), wird NEUEN,
  noch nicht abonnierten Besuchern auf der Kanal-Startseite gezeigt. Muss in
  wenigen Saetzen zeigen, was den Zuschauer hier erwartet (Art der Inhalte,
  Haeufigkeit), energiegeladen und einladend, endet mit einer klaren aber
  nicht plumpen Aufforderung zu abonnieren. Ein einziger Absatz, keine
  Zeilenumbrueche. Sprache: {config.CHANNEL_LANGUAGE}.
- "title": Kurzer Video-Titel fuer den Trailer (z.B. "Willkommen auf dem Kanal!")
- "description": 2-3 Saetze Beschreibung des Kanals fuers Video
"""

    resp = client.messages.create(
        model=config.ANTHROPIC_MODEL,
        max_tokens=1500,
        system=system,
        tools=[_TRAILER_TOOL],
        tool_choice={"type": "tool", "name": "submit_trailer"},
        messages=[{"role": "user", "content": "Erzeuge das Kanal-Trailer-Skript."}],
    )

    tool_call = next((b for b in resp.content if b.type == "tool_use"), None)
    if tool_call is None:
        raise RuntimeError("Keine Tool-Antwort fuer Trailer-Skript erhalten")

    return tool_call.input


if __name__ == "__main__":
    facts = generate_facts(config.SHORTS_PER_RUN)
    for f in facts:
        print("=" * 60)
        print(f["title"])
        print(f["script"])
    state.record_facts(facts)
