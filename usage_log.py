"""Kosten-Protokoll (seit 23.11.2026): jeder Claude-Aufruf der Pipelines schreibt Modell und Token-Verbrauch
in die Supabase-Tabelle api_usage. Damit sieht Robin im Dashboard/Supabase, welche Pipeline was kostet.

Wird einmal beim Import von config.py aktiviert (patcht Messages.create und den Stream-Abschluss).
Fehler beim Protokollieren werden still ignoriert – die Pipeline läuft immer weiter.
"""
import os
import sys
import threading

import requests

URL = "https://kfiiaqjzzwwacthzrcut.supabase.co/rest/v1/api_usage"
# öffentlicher Schlüssel (nur Einfügen erlaubt, per RLS abgesichert)
KEY = "sb_publishable_TvKHtP7D400zXCBFj4icPA_c7T6Hosp"


def _pipeline():
    wf = os.environ.get("GITHUB_WORKFLOW") or "lokal"
    try:
        root = os.path.dirname(os.path.abspath(__file__))
        script = os.path.relpath(os.path.abspath(sys.argv[0]), root).replace("\\", "/")
    except Exception:  # noqa: BLE001
        script = ""
    return f"{wf} | {script}"[:80]


def _send(row):
    try:
        requests.post(URL, json=row, headers={"apikey": KEY, "Authorization": f"Bearer {KEY}",
                                               "Content-Type": "application/json", "Prefer": "return=minimal"}, timeout=8)
    except Exception:  # noqa: BLE001
        pass


def log(resp, pipeline=None):
    try:
        u = getattr(resp, "usage", None)
        if u is None:
            return
        stu = getattr(u, "server_tool_use", None)
        row = {
            "pipeline": (pipeline or _pipeline())[:80],
            "model": getattr(resp, "model", None),
            "input_tokens": getattr(u, "input_tokens", None),
            "output_tokens": getattr(u, "output_tokens", None),
            "cache_read_tokens": getattr(u, "cache_read_input_tokens", None),
            "cache_write_tokens": getattr(u, "cache_creation_input_tokens", None),
            "web_searches": getattr(stu, "web_search_requests", 0) if stu else 0,
        }
        t = threading.Thread(target=_send, args=(row,), daemon=False)
        t.start()
    except Exception:  # noqa: BLE001
        pass


_installed = False


def install():
    global _installed
    if _installed:
        return
    _installed = True
    try:
        from anthropic.resources.messages import Messages
        orig = Messages.create

        def create(self, *a, **kw):
            r = orig(self, *a, **kw)
            if not kw.get("stream"):
                log(r)
            return r
        Messages.create = create
    except Exception:  # noqa: BLE001
        pass
    try:
        from anthropic.lib.streaming import MessageStream
        orig_final = MessageStream.get_final_message

        def get_final_message(self):
            m = orig_final(self)
            log(m)
            return m
        MessageStream.get_final_message = get_final_message
    except Exception:  # noqa: BLE001
        pass
