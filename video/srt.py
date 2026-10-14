"""Baut eine echte SRT-Untertiteldatei aus Wort-Timestamps (fuer den YouTube-
Untertitel-Upload - durchsuchbares Transkript, Barrierefreiheit, kostenlose
automatische Uebersetzung durch YouTube in andere Sprachen)."""


def _fmt_srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _chunk_words(words: list[dict], size: int) -> list[dict]:
    chunks = []
    for i in range(0, len(words), size):
        group = words[i : i + size]
        chunks.append(
            {
                "text": " ".join(w["text"] for w in group),
                "start": group[0]["start"],
                "end": group[-1]["end"],
            }
        )
    return chunks


def build_srt(words: list[dict], out_path: str, words_per_line: int = 9) -> str:
    chunks = _chunk_words(words, words_per_line)
    lines = []
    for i, c in enumerate(chunks, start=1):
        lines.append(str(i))
        lines.append(f"{_fmt_srt_time(c['start'])} --> {_fmt_srt_time(c['end'])}")
        lines.append(c["text"])
        lines.append("")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return out_path
