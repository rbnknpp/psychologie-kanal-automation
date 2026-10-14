"""Baut ein kurzes, animiertes Marken-Intro (rotierender Verlauf + Icon-Pop +
Text-Einblendung + generierter Jingle) fuer Longform-Videos.

Komplett per ffmpeg/Pillow erzeugt - keine externen Assets, keine Lizenzfragen.
"""
import subprocess

import config
from video.icon_asset import get_or_build_icon


def _escape_drawtext(text: str) -> str:
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "’")


def build_intro(out_path: str, duration: float = 3.0) -> str:
    width, height = config.LONGFORM_RESOLUTION
    c0, c1 = config.INTRO_COLORS
    text = _escape_drawtext(config.INTRO_CHANNEL_NAME)
    fade_start = duration - 0.6
    icon_path = get_or_build_icon()

    video_filter = (
        f"gradients=s={width}x{height}:c0=0x{c0}:c1=0x{c1}:d={duration}:speed=0.06:type=radial,"
        f"fps={config.FPS},format=yuva420p[bg];"
        f"[0:v]scale=340:340,fade=t=in:st=0:d=0.3:alpha=1,format=rgba[icon];"
        f"[bg][icon]overlay=x=(W-w)/2:"
        f"y='if(lt(t,0.5), 90-50*(1-t/0.5)*(1-t/0.5), 90)':format=auto[bg2];"
        f"[bg2]drawtext=text='{text}':fontcolor=white:fontsize=80:"
        f"fontfile='{config.DRAWTEXT_FONT_FILE}':"
        f"x=(w-text_w)/2:"
        f"y='if(lt(t,0.5), 560+60*(1-t/0.5)*(1-t/0.5), 560)':"
        f"alpha='if(lt(t,0.5),t/0.5,if(gt(t,{fade_start}),max(0,({duration}-t)/0.6),1))',"
        f"format=yuv420p[v]"
    )
    audio_filter = (
        f"sine=frequency=110:duration=0.15,afade=t=out:st=0:d=0.15,volume=0.6[thump];"
        f"sine=frequency=523.25:duration={duration}[a1];"
        f"sine=frequency=659.25:duration={duration}[a2];"
        f"sine=frequency=783.99:duration={duration}[a3];"
        f"[a1][a2][a3]amix=inputs=3:duration=longest,"
        f"afade=t=in:d=0.25,afade=t=out:st={fade_start}:d=0.6,volume=0.4[chime];"
        f"[thump][chime]amix=inputs=2:duration=longest[a]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-t", str(duration), "-i", icon_path,
        "-filter_complex", f"{video_filter};{audio_filter}",
        "-map", "[v]", "-map", "[a]",
        "-t", str(duration),
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        out_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path


def get_or_build_intro() -> str:
    """Baut das Intro einmalig und cached es in assets/."""
    intro_path = config.ASSETS_DIR / "intro.mp4"
    if not intro_path.exists():
        build_intro(str(intro_path))
    return str(intro_path)
