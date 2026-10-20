"""Generiert Fakten-Skripte fuer Shorts via Claude API (Tool-Use fuer garantiert valides JSON)."""
import json

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
    return f"\nZusaetzliche Erkenntnisse aus den echten Zuschauerzahlen der letzten Wochen:\n{digest}\n"


_SYSTEM_PROMPT = """Du bist Autor fuer einen YouTube-Kanal mit folgender Ausrichtung:
{niche}
{performance_block}

Rufe das Tool "submit_facts" GENAU EINMAL auf, mit allen angeforderten Fakten darin.

Pro Fakt:
- "title": YouTube-Titel, HOECHSTENS 60 Zeichen, beschreibt eine konkrete Alltagssituation des
  Zuschauers in der Du-Form (z.B. "Warum du beim Luegen die Haende versteckst",
  "Dein Gehirn erkennt falsches Laecheln sofort"). Keine abstrakten Fakten ohne Alltagsbezug,
  kein Clickbait der nicht eingeloest wird
- "thumbnail_text": SEHR kurze knackige Kernaussage fuers Titelbild, 2-5 Woerter,
  komplett anders formuliert als der Titel (keine Wiederholung), wirkt wie ein Schlagwort
  (z.B. "DEIN GEHIRN LUEGT", "DER 5-EURO-FEHLER")
- "hook": der allererste gesprochene Satz (max 12 Woerter), muss in 2 Sekunden fesseln
- "script": der komplette gesprochene Text (inkl. hook am Anfang), 155-185 Woerter (Ziel: 55-62 Sekunden),
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
- Themen-Schwerpunkt (Robin 20.10.2026, aus den Kanal-Zahlen): mindestens 4 von 6 Fakten aus
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


def _request_facts(client, n, avoid_block):
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
        messages=[{"role": "user", "content": f"Erzeuge {n} neue, unterschiedliche Fakten.\n\n{avoid_block}"}],
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


def generate_facts(n: int, max_attempts: int = 3, extra_avoid: list[str] = None) -> list[dict]:
    """Fragt Claude nach n neuen, bisher ungenutzten Fakten-Skripten (mit Retry).

    `extra_avoid` sind zusaetzliche Titel (z.B. die in diesem Lauf bereits fuer
    die Shorts generierten Fakten), die NICHT nochmal auftauchen sollen -
    unabhaengig vom gespeicherten state.json-Verlauf.
    """
    if not config.ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY fehlt in .env")

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    avoid = state.recent_titles(50) + list(extra_avoid or [])
    avoid_block = (
        "Vermeide diese bereits verwendeten Themen/Titel (keine Wiederholung, auch nicht umformuliert):\n"
        + "\n".join(f"- {t}" for t in avoid)
        if avoid
        else "Es gibt noch keine bisherigen Themen."
    )

    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            return _request_facts(client, n, avoid_block)
        except ValueError as e:
            last_error = e
            print(f"  Versuch {attempt}/{max_attempts} fehlgeschlagen: {e}")

    raise RuntimeError(f"Skriptgenerierung nach {max_attempts} Versuchen fehlgeschlagen: {last_error}")


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
