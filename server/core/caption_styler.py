"""
OpusClip / OpenClipper style animated caption generator.
Produces Advanced SubStation Alpha (.ass) subtitles with word-by-word karaoke highlights.
"""

from typing import List, Dict, Any
from pathlib import Path


def generate_animated_ass(
    segments: List[Any],
    output_ass_path: str,
    font_name: str = "Arial Black",
    font_size: int = 24,
    primary_color: str = "&H00FFFFFF",      # White
    highlight_color: str = "&H0000FFFF",    # Yellow highlight
    outline_color: str = "&H00000000",      # Black outline
    outline_width: int = 3
) -> str:
    """
    Generates ASS subtitle with karaoke tags (\\k) for active-word highlighting.
    """
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{font_size},{primary_color},{highlight_color},{outline_color},&H80000000,-1,0,0,0,100,100,1,0,1,{outline_width},2,2,40,40,240,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    def fmt_ass_time(seconds: float) -> str:
        cs = int((seconds % 1) * 100)
        s = int(seconds) % 60
        m = (int(seconds) // 60) % 60
        h = int(seconds) // 3600
        return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

    event_lines = []

    for seg in segments:
        words = getattr(seg, "words", []) or []
        if not words:
            # Fallback if no word timestamps
            start_str = fmt_ass_time(seg.start)
            end_str = fmt_ass_time(seg.end)
            event_lines.append(f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{seg.text.upper()}")
            continue

        # Group words into chunks of 3-5 words for fast-paced vertical shorts reading
        chunk_size = 4
        for i in range(0, len(words), chunk_size):
            chunk = words[i:i + chunk_size]
            chunk_start = chunk[0].start
            chunk_end = chunk[-1].end
            
            karaoke_text = ""
            for w in chunk:
                dur_cs = max(1, int((w.end - w.start) * 100))
                karaoke_text += f"{{\\k{dur_cs}}}{w.word.upper()} "

            start_str = fmt_ass_time(chunk_start)
            end_str = fmt_ass_time(chunk_end)
            event_lines.append(f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{karaoke_text.strip()}")

    Path(output_ass_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_ass_path, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(event_lines))

    return output_ass_path
