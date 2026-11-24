"""Schreibt die nächste Was-wäre-wenn-Folge (Recherche mit Websuche + Skript als JSON).

Themen: waswaerewenn/themen.json (Liste, oben = als Nächstes). Erledigte Themen: waswaerewenn/state.json.
Ergebnis: waswaerewenn/folgen/NNN_slug.json im Format von 001_erde_stoppt.json.
"""
import datetime as dt
import json
import re
from pathlib import Path

import anthropic

import config

HERE = Path(__file__).resolve().parent
THEMEN = HERE / "themen.json"
STATE = HERE / "state.json"
FOLGEN = HERE / "folgen"
FIGUR = "Leo, a 10 year old cartoon boy with messy brown hair, big curious eyes, orange hoodie and blue jeans"

SYSTEM = """Du schreibst Folgen für den deutschen YouTube-Kanal „Was wäre wenn" (animierte Gedankenexperimente, 3D-Cartoon-Look, Hauptfigur Leo, 10 Jahre).
Ton: spannend, warm, mit Humor, für Jugendliche und Erwachsene verständlich, du sprichst die Zuschauer mit „du" an.
Inhalt: Ein Szenario Schritt für Schritt durchspielen, mit echten, belegten Fakten aus Wissenschaft und Alltag (Zahlen nur, wenn sie stimmen; Spekulation klar als Vermutung kennzeichnen).
Jede Folge ist eigenständig und originell – keine Schablone, keine Füllsätze. Keine Gewaltdarstellung, kein Horror, kinderfreundliche Bilder."""

FORMAT = """Gib NUR ein JSON-Objekt zurück:
{
 "titel": "Was wäre, wenn …?  (max. 70 Zeichen, neugierig, kein Clickbait-Lügen)",
 "thumb_text": "2-3 Wörter in GROSSBUCHSTABEN mit ?! (z. B. ERDE STOPPT?!)",
 "thumb_bild": "englische Bildbeschreibung fürs Titelbild: Leo mit starkem Gesichtsausdruck + das Szenario, dramatisch",
 "beschreibung": "YouTube-Beschreibung: 2-3 Sätze, dann 'Welches Szenario sollen wir als Nächstes durchspielen? Schreib es in die Kommentare!', dann 5 Hashtags inkl. #waswärewenn",
 "tags": ["was wäre wenn", "... 6-10 weitere"],
 "figur": "%s",
 "szenen": [ {"say": "1-2 gesprochene Sätze", "bild": "englische Szene ODER gleich", "titel": "optional kurze Texteinblendung"} ],
 "shorts": [ {"von": 0, "bis": 0, "titel": "eigenständige Schlagzeile für den Short, max. 60 Zeichen (steht ohne „Was wäre, wenn“ davor)"} ]
}
Regeln:
- 85-110 Szenen, zusammen 1250-1450 gesprochene Wörter (ergibt 8,5-10 Minuten). Das ist Pflicht.
- Aufbau: Hook-Alltagsszene mit Leo -> Teaser „In diesem Video …" -> 6-8 Kapitel mit eigener Einblendung (titel) -> überraschende Wendung/Realitäts-Check -> Abschluss mit Frage an die Zuschauer + Abo-Hinweis.
- "bild": eine konkrete englische Szene im Pixar-Stil (Leo kommt in jedem 3. Bild vor, immer mit dem Wort "Leo"), "gleich" = vorheriges Bild bleibt (max. 3x hintereinander). 50-65 verschiedene Bilder. Keine Schrift in Bildern. Planeten/Erde ohne Ringe. Das Bild muss sachlich zum Gesagten passen (richtige Tierart, z. B. Grönlandhai statt Weißer Hai; keine Planeten unter Wasser oder Weltraum-Deko in Erdszenen).
- "titel": nur bei Kapitelstarts und starken Zahlen (z. B. "1.670 km/h"), max. 22 Zeichen, sonst weglassen.
- Zahlen im "say" ausschreiben, wie man sie spricht.
- "shorts": 6-8 Abschnitte (von/bis = Szenen-Indizes, 0-basiert), je 25-55 Sekunden (ca. 70-140 Wörter), jeder für sich verständlich, starker erster Satz.""" % FIGUR


def load(p, default):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def next_episode():
    themen = load(THEMEN, [])
    st = load(STATE, {"erledigt": []})
    done = {x["thema"] for x in st["erledigt"]}
    thema = next((t for t in themen if t not in done), None)
    nr = len(list(FOLGEN.glob("*.json"))) + 1
    client = anthropic.Anthropic()
    hint = f"Thema: „{thema}“." if thema else f"Wähle selbst ein neues, packendes Szenario. Bisher: {sorted(done)}."
    # 1) Recherche
    msgs = [{"role": "user", "content": f"{hint}\nRecherchiere mit der Websuche 15-25 belegte, überraschende Fakten (mit Zahlen und Quellen) für diese Folge. Gib nur das Faktenblatt aus."}]
    parts = []
    for _ in range(5):
        r = client.messages.create(model=config.ANTHROPIC_MODEL, max_tokens=8000, system=SYSTEM,
                                   tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 8}], messages=msgs)
        parts += [b.text for b in r.content if b.type == "text"]
        if r.stop_reason == "pause_turn":
            msgs = msgs + [{"role": "assistant", "content": r.content}]
            continue
        break
    facts = "".join(parts)
    # 2) Skript
    last = ""
    for attempt in range(3):
        # Skript ist reines Schreiben: ohne Denkphase, damit das Token-Budget fürs JSON reicht
        with client.messages.stream(model=config.ANTHROPIC_MODEL, max_tokens=48000, system=SYSTEM,
                                    thinking={"type": "disabled"}, messages=[{
                "role": "user", "content": f"{hint}\n\nFaktenblatt:\n{facts}\n\n{FORMAT}"}]) as s:
            msg = s.get_final_message()
        last = "".join(b.text for b in msg.content if b.type == "text")
        print(f"  Antwort: stop={msg.stop_reason}, {len(last)} Zeichen, Ausgabe-Tokens {msg.usage.output_tokens}")
        try:
            ep = json.loads(last[last.find("{"):last.rfind("}") + 1])
            words = sum(len(x["say"].split()) for x in ep["szenen"])
            print(f"Skript: {ep['titel']} – {len(ep['szenen'])} Szenen, {words} Wörter")
            if words < 1150:
                print("  zu kurz, neuer Versuch")
                continue
            ep["figur"] = FIGUR
            slug = re.sub(r"[^a-z0-9]+", "_", (thema or ep["titel"]).lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss"))[:40].strip("_")
            path = FOLGEN / f"{nr:03d}_{slug}.json"
            path.write_text(json.dumps(ep, ensure_ascii=False, indent=1), encoding="utf-8")
            st["erledigt"].append({"thema": thema or ep["titel"], "datei": path.name, "datum": dt.date.today().isoformat()})
            STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
            return path
        except Exception as e:  # noqa: BLE001
            print("  JSON-Fehler:", e)
    raise RuntimeError(f"keine gültige Folge: {last[-300:]}")
