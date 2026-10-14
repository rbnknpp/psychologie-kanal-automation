"""Einfacher persistenter Zustand: welche Themen/Titel wurden schon verwendet."""
import json
from config import STATE_FILE

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


def recent_titles(n=50):
    return load()["used_titles"][-n:]
