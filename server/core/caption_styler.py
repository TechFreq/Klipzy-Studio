"""
Animated caption generator with 20+ professional styling presets.
Produces Advanced SubStation Alpha (.ass) subtitles with word-by-word karaoke highlights.
"""

from typing import List, Dict, Any, Optional
from pathlib import Path
from server.core.caption_presets import CAPTION_PRESETS, get_available_presets


def generate_animated_ass(
    segments: List[Any],
    output_ass_path: str,
    style_preset: str = "viral_yellow",
    font_name: Optional[str] = None,
    font_size: Optional[int] = None,
    primary_color: Optional[str] = None,
    highlight_color: Optional[str] = None,
    outline_color: Optional[str] = None,
    outline_width: Optional[int] = None,
    chunk_size: int = 4,
    play_res_x: int = 1080,
    play_res_y: int = 1920,
) -> str:
    """
    Generates ASS subtitle with karaoke tags (\\k) for active-word highlighting.
    Supports 20+ preset styles with individual property overrides.
    """
    preset = CAPTION_PRESETS.get(style_preset, CAPTION_PRESETS["viral_yellow"])

    f_name = font_name or preset.get("font_name", "Arial Black")
    f_size = font_size or preset.get("font_size", 26)
    p_color = primary_color or preset.get("primary_color", "&H00FFFFFF")
    h_color = highlight_color or preset.get("highlight_color", "&H0000FFFF")
    o_color = outline_color or preset.get("outline_color", "&H00000000")
    b_color = preset.get("back_color", "&H80000000")
    o_width = outline_width if outline_width is not None else preset.get("outline_width", 3)
    s_width = preset.get("shadow_width", 2)
    border_style = preset.get("border_style", 1)
    margin_v = preset.get("margin_v", 240)

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {play_res_x}
PlayResY: {play_res_y}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{f_name},{f_size},{p_color},{h_color},{o_color},{b_color},-1,0,0,0,100,100,1,0,{border_style},{o_width},{s_width},2,40,40,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    def fmt_ass_time(seconds: float) -> str:
        cs = int(round((seconds % 1) * 100))
        if cs >= 100:
            cs = 99
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
            clean_text = str(seg.text).upper().replace("{", "").replace("}", "")
            event_lines.append(f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{clean_text}")
            continue

        # Group words into chunks of 3-5 words for fast-paced vertical shorts reading
        for i in range(0, len(words), chunk_size):
            chunk = words[i:i + chunk_size]
            chunk_start = chunk[0].start
            chunk_end = chunk[-1].end
            
            karaoke_text = ""
            for w in chunk:
                dur_cs = max(1, int(round((w.end - w.start) * 100)))
                w_text = getattr(w, "word", str(w)).replace("{", "").replace("}", "").upper()
                karaoke_text += f"{{\\k{dur_cs}}}{w_text} "

            start_str = fmt_ass_time(chunk_start)
            end_str = fmt_ass_time(chunk_end)
            event_lines.append(f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{karaoke_text.strip()}")

    Path(output_ass_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_ass_path, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(event_lines))

    return output_ass_path

