"""Kanal-Schalter aus dem Agent-HQ (Seite „Kanäle & Projekte“, Tabelle projects).

Robin 24.11.2026: Der Schalter steuert die Pipelines wirklich. Ruht ein Kanal, wird für ihn
nichts erzeugt und nichts hochgeladen. Bei Netzwerkfehlern läuft alles normal weiter (fail-open).
"""
import json
import sys
import urllib.request

_URL = "https://kfiiaqjzzwwacthzrcut.supabase.co/rest/v1/rpc/project_status_all"
_KEY = "sb_publishable_TvKHtP7D400zXCBFj4icPA_c7T6Hosp"  # öffentlicher Schlüssel (steht auch im Dashboard)
_cache: dict | None = None


def status() -> dict:
    global _cache
    if _cache is None:
        try:
            req = urllib.request.Request(_URL, data=b"{}", method="POST",
                                         headers={"apikey": _KEY, "Authorization": f"Bearer {_KEY}", "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as r:
                _cache = json.loads(r.read().decode()) or {}
        except Exception as e:  # noqa: BLE001
            print(f"Kanal-Schalter nicht erreichbar ({e}) – laufe normal weiter.")
            _cache = {}
    return _cache


def aktiv(pid: str) -> bool:
    return status().get(pid, "aktiv") == "aktiv"


if __name__ == "__main__":
    # Für GitHub-Workflows: python projekt.py yt-pflege tt-pflege → Exit 0, wenn mindestens einer aktiv ist
    ids = sys.argv[1:]
    on = [p for p in ids if aktiv(p)]
    print("aktiv:", on or "keiner", "| ruhend:", [p for p in ids if p not in on] or "keiner")
    sys.exit(0 if on else 3)
