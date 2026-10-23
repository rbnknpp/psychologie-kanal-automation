"""Verbindet die YouTube-Analytics-Zahlen mit den lokal erzeugten Fakten
(fact.json der jeweiligen Pipeline) und schreibt eine kompakte Erfolgs-/
Flop-Zusammenfassung, die die Skript-Generierung (content/script_generator.py)
beim naechsten Lauf als Zusatz-Anweisung liest.

Laeuft als Teil von weekly_report.py mit, kann aber auch einzeln aufgerufen
werden: python performance_insights.py
Seit 23.10.2026 zusaetzlich taeglich vor der Skript-Generierung (daily.yml), nur Psychologie-Kanal,
10 Tage Rueckblick - damit die Themen-Auswahl auf frische Zahlen reagiert statt einmal pro Woche.
"""
import datetime
import json
from pathlib import Path

import config
from auth import get_credentials
from analytics import get_ranked_videos

LOOKBACK_DAYS = 14
TOP_N = 8
FLOP_N = 5
MIN_VIEWS_FOR_FLOP = 15  # unter dieser Schwelle ist "schlecht" oft nur "kaum Zeit gehabt"
MIN_AGE_DAYS_FOR_FLOP = 3  # frisch hochgeladene Videos nicht faelschlich als Flop werten


def _load_local_facts(pipeline_dir: Path) -> dict[str, dict]:
    """Liest alle fact.json-Dateien der letzten Laeufe ein, Titel -> Fakt-Daten."""
    by_title = {}
    history = pipeline_dir / "fact_history.json"  # von der Cloud-Pipeline fortgeschrieben (state.record_facts)
    if history.exists():
        try:
            by_title.update(json.loads(history.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass
    output_dir = pipeline_dir / "output"
    if not output_dir.exists():
        return by_title
    for fact_path in output_dir.glob("*/*/*/fact.json"):
        try:
            fact = json.loads(fact_path.read_text(encoding="utf-8"))
            by_title[fact["title"]] = fact
        except (json.JSONDecodeError, KeyError, OSError):
            continue
    # Longform-Kompilationen: Titel liegt in compilation_metadata.json, nicht fact.json
    for meta_path in output_dir.glob("*/*_metadata.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            by_title[meta["title"]] = {
                "title": meta["title"],
                "thumbnail_text": meta["title"],
                "hook": None,
                "tags": meta.get("tags", []),
                "is_longform_compilation": True,
            }
        except (json.JSONDecodeError, KeyError, OSError):
            continue
    return by_title


def _describe(entry: dict, fact: dict | None) -> str:
    parts = [f'"{entry["title"]}" ({entry["views"]} Views, {entry["retention"]}% Bindung)']
    if fact:
        if fact.get("hook"):
            parts.append(f'Hook: "{fact["hook"]}"')
        if fact.get("tags"):
            parts.append("Themen: " + ", ".join(fact["tags"][:4]))
    return " | ".join(parts)


def build_insights(creds, pipeline_dir: Path, days: int = LOOKBACK_DAYS) -> dict:
    end_date = datetime.date.today()
    start_date = end_date - datetime.timedelta(days=days)
    top = get_ranked_videos(creds, start_date.isoformat(), end_date.isoformat(), order="top", max_results=TOP_N)
    flop = get_ranked_videos(
        creds, start_date.isoformat(), end_date.isoformat(),
        order="bottom", max_results=FLOP_N, min_views=MIN_VIEWS_FOR_FLOP,
        min_age_days=MIN_AGE_DAYS_FOR_FLOP,
    )

    local_facts = _load_local_facts(pipeline_dir)

    top_lines = [_describe(e, local_facts.get(e["title"])) for e in top]
    flop_lines = [_describe(e, local_facts.get(e["title"])) for e in flop]

    digest_parts = []
    if top_lines:
        digest_parts.append(
            "Diese Videos kamen in den letzten " + str(days) + " Tagen gut an (hohe Aufrufe/Zuschauerbindung) "
            "- greife aehnliche Themen, Hook-Formulierungen oder Erzaehlaufbauten wieder auf, "
            "aber nicht wortgleich:\n" + "\n".join(f"- {l}" for l in top_lines)
        )
    if flop_lines:
        digest_parts.append(
            "Diese Videos kamen vergleichsweise schlecht an - vermeide aehnliche Themen/Formulierungen:\n"
            + "\n".join(f"- {l}" for l in flop_lines)
        )

    return {
        "generated_at": datetime.datetime.now().isoformat(),
        "lookback_days": days,
        "top": top,
        "flop": flop,
        "digest_text": "\n\n".join(digest_parts),
    }


def run(only: str = None, days: int = LOOKBACK_DAYS) -> None:
    for ch in config.CHANNELS:
        if only and only.lower() not in ch["label"].lower():
            continue
        print(f"Erfolgsmuster fuer: {ch['label']}...")
        creds = get_credentials(ch["token_file"])
        insights = build_insights(creds, ch["pipeline_dir"], days)
        out_path = ch["insights_file"]
        out_path.write_text(json.dumps(insights, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  {len(insights['top'])} Top-, {len(insights['flop'])} Flop-Videos -> {out_path}")


if __name__ == "__main__":
    # Taeglicher Lauf (daily.yml): python performance_insights.py psychologie 10
    import sys
    run(sys.argv[1] if len(sys.argv) > 1 else None, int(sys.argv[2]) if len(sys.argv) > 2 else LOOKBACK_DAYS)
