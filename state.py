"""Einfacher persistenter Zustand: welche Themen/Titel wurden schon verwendet."""
import json
from config import ROOT, STATE_FILE

HISTORY_FILE = ROOT / "fact_history.json"  # Titel -> Hook/Tags; vom woechentlichen Bericht genutzt (report/performance_insights.py)

_DEFAULT = {"used_titles": [], "used_topics": []}


def load():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return dict(_DEFAULT)


def save(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def record_facts(facts):
    state = load()
    for f in facts:
        state["used_titles"].append(f["title"])
    # nur die letzten 200 merken, damit der Prompt-Kontext klein bleibt
    state["used_titles"] = state["used_titles"][-200:]
    save(state)

    history = json.loads(HISTORY_FILE.read_text(encoding="utf-8")) if HISTORY_FILE.exists() else {}
    for f in facts:
        history[f["title"]] = {"title": f["title"], "hook": f.get("hook"), "tags": f.get("tags", [])}
    history = dict(list(history.items())[-400:])
    HISTORY_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=1), encoding="utf-8")


def recent_titles(n=50):
    return load()["used_titles"][-n:]
