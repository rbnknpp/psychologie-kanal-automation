"""Baut ein .ass Untertitel-File aus Wort-Timestamps der TTS-Engine."""
import config


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


def _fmt_time(t: float) -> str:
    cs = int(round(t * 100))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


_ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{fontsize},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,5,0,{alignment},40,40,{marginv},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def build_ass(
    alignment: dict,
    out_path: str,
    resolution: tuple[int, int],
    font_size: int,
    words_per_chunk: int = None,
    alignment_pos: int = 5,
    margin_v: int = 0,
) -> str:
    chunks = _chunk_words(alignment["words"], words_per_chunk or config.WORDS_PER_CAPTION_CHUNK)

    width, height = resolution
    header = _ASS_HEADER.format(
        width=width,
        height=height,
        font=config.CAPTION_FONT,
        fontsize=font_size,
        alignment=alignment_pos,
        marginv=margin_v,
    )

    lines = [header]
    for c in chunks:
        text = c["text"].upper().replace("\n", " ")
        lines.append(
            f"Dialogue: 0,{_fmt_time(c['start'])},{_fmt_time(c['end'])},Default,,0,0,0,,{text}\n"
        )

    with open(out_path, "w", encoding="utf-8") as f:
        f.writelines(lines)

    return out_path
