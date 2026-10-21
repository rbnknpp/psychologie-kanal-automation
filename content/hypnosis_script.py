"""Generiert das Skript fuer das woechentliche Einschlaf-Hypnose-Video via Claude API.

Liefert Abschnitte mit Pausenlaenge, damit die Sprachausgabe langsam und mit
Atempausen klingt. Keine Heilversprechen, keine medizinischen Aussagen.
"""
import anthropic

import config

_SYSTEM_PROMPT = """Du schreibst deutschsprachige Einschlaf-Hypnosen (geführte Entspannung) für den YouTube-Kanal „{channel}“.

Regeln:
- Ruhig, warm, langsam, Du-Form, wie ein sanftes, vertrautes Gespräch. Kurze Sätze. Viele Wiederholungen von Atmung, Schwere, Wärme, Sinken.
- Aufbau: Ankommen und Atmung → Körperreise von Kopf bis Fuß → innerer sicherer Ort (Natur-Bild, z. B. Strand, Wald, Bergsee) → immer tiefer sinken (z. B. Treppe mit 10 Stufen) → Ausklang in den Schlaf, Stimme wird leiser. KEIN Aufwecken am Ende.
- Keine Heilversprechen, keine Diagnosen, keine Aussagen zu Krankheiten oder Medikamenten. Keine Markennamen.
- Die Sprachausgabe liest jeden Abschnitt vor, danach folgt eine Stille. Schreibe nur gesprochenen Text: keine Regieanweisungen, keine Klammern, keine Sonderzeichen außer Satzzeichen.
- Gesamtlänge des gesprochenen Textes: etwa {words} Wörter, verteilt auf {sections} bis {sections_max} Abschnitte.
- Pausen: am Anfang 3–5 Sekunden, später länger (6–12 Sekunden), in der letzten Phase 12–20 Sekunden.
- Wähle jede Woche ein anderes Bild für den sicheren Ort und einen anderen Schwerpunkt (z. B. Gedankenkarussell stoppen, Schwere, Wärme, Geborgenheit). Bereits verwendete Themen: {avoid}."""

_TOOL = {
    "name": "submit_hypnosis",
    "description": "Gibt das fertige Hypnose-Skript zurück.",
    "input_schema": {
        "type": "object",
        "properties": {
            "theme": {"type": "string", "description": "Kurzes Thema, z. B. 'Bergsee und Schwere'"},
            "title": {"type": "string", "description": "YouTube-Titel, max. 90 Zeichen, mit Nutzen, z. B. 'Sofort einschlafen: …'"},
            "thumbnail_text": {"type": "string", "description": "2–4 Wörter für das Thumbnail"},
            "description": {"type": "string", "description": "2–3 ruhige Sätze, worum es geht"},
            "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 15},
            "sections": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "phase": {"type": "string", "description": "Ankommen | Körperreise | Sicherer Ort | Vertiefung | Einschlafen"},
                        "text": {"type": "string"},
                        "pause_after_seconds": {"type": "number"},
                    },
                    "required": ["phase", "text", "pause_after_seconds"],
                },
            },
        },
        "required": ["theme", "title", "thumbnail_text", "description", "tags", "sections"],
    },
}


_MORE_TOOL = {
    "name": "submit_more",
    "description": "Gibt zusätzliche Abschnitte zurück, die vor dem Ausklang eingefügt werden.",
    "input_schema": {
        "type": "object",
        "properties": {"sections": _TOOL["input_schema"]["properties"]["sections"]},
        "required": ["sections"],
    },
}


def _word_count(script: dict) -> int:
    return sum(len(s.get("text", "").split()) for s in script.get("sections", []))


def _extend(client, script: dict, words: int, rounds: int = 4) -> dict:
    """Claude schreibt in einem Rutsch meist nur ~1.000 Woerter (21.10.2026: Stimme nur 10,5 Min).
    Darum so lange weitere Vertiefungs-Abschnitte vor dem Ausklang einfuegen, bis ~85 % der Ziellaenge stehen."""
    for r in range(rounds):
        have = _word_count(script)
        if have >= words * 0.85:
            break
        secs = script["sections"]
        keep_end = min(3, max(1, len(secs) // 6))       # Ausklang bleibt am Ende
        body, ending = secs[:-keep_end], secs[-keep_end:]
        context = "\n\n".join(s["text"] for s in body[-3:])
        need = min(1400, words - have)
        try:
            resp = client.messages.create(
                model=config.ANTHROPIC_MODEL, max_tokens=8000,
                system=_SYSTEM_PROMPT.format(channel=config.INTRO_CHANNEL_NAME, words=need, sections=max(6, need // 120),
                                             sections_max=max(6, need // 120) + 6, avoid="keine"),
                tools=[_MORE_TOOL], tool_choice={"type": "tool", "name": "submit_more"},
                messages=[{"role": "user", "content": (
                    f"Thema: {script.get('theme')}. Die Hypnose ist zu kurz. Schreibe etwa {need} weitere Wörter als neue "
                    "Abschnitte, die nahtlos an diese Stelle anschließen und noch tiefer führen (Phase „Sicherer Ort“ und "
                    "„Vertiefung“: mehr Sinneseindrücke, Atmung, Schwere, Wärme, langsames Zählen). Kein Ausklang, kein "
                    f"Aufwecken – der Ausklang folgt danach.\n\nLetzte Abschnitte bisher:\n{context}")}],
            )
            call = next((b for b in resp.content if b.type == "tool_use"), None)
            more = (call.input.get("sections") if call else None) or []
        except Exception as e:
            print(f"  Hypnose-Verlaengerung {r + 1} fehlgeschlagen: {e}")
            break
        if not more:
            break
        script["sections"] = body + more + ending
        print(f"  Hypnose verlaengert: {have} -> {_word_count(script)} Woerter")
    return script


def generate_hypnosis_script(avoid_themes: list[str] | None = None, words: int = None, max_attempts: int = 3) -> dict:
    words = words or config.HYPNOSIS_SCRIPT_WORDS
    sections = max(8, words // 120)
    client = anthropic.Anthropic()
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            resp = client.messages.create(
                model=config.ANTHROPIC_MODEL,
                max_tokens=min(64000, 4000 + words * 3),
                system=_SYSTEM_PROMPT.format(
                    channel=config.INTRO_CHANNEL_NAME, words=words, sections=sections,
                    sections_max=sections + 10, avoid=", ".join(avoid_themes or []) or "keine",
                ),
                tools=[_TOOL],
                tool_choice={"type": "tool", "name": "submit_hypnosis"},
                messages=[{"role": "user", "content": "Schreibe die Einschlaf-Hypnose für diese Woche."}],
            )
            if resp.stop_reason == "max_tokens":
                raise ValueError("Antwort abgeschnitten (max_tokens)")
            call = next((b for b in resp.content if b.type == "tool_use"), None)
            if call is None:
                raise ValueError("Keine Tool-Antwort")
            script = call.input
            if len(script.get("sections", [])) < 5:
                raise ValueError(f"Zu wenige Abschnitte: {len(script.get('sections', []))}")
            return _extend(client, script, words)
        except Exception as e:
            last_error = e
            print(f"  Hypnose-Skript Versuch {attempt}/{max_attempts} fehlgeschlagen: {e}")
    raise RuntimeError(f"Hypnose-Skript fehlgeschlagen: {last_error}")
