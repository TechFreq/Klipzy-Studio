"""
Animated caption generator with 20+ professional styling presets.
Produces Advanced SubStation Alpha (.ass) subtitles with word-by-word karaoke highlights.
"""

from typing import List, Dict, Any, Optional
from pathlib import Path
from server.core.caption_presets import CAPTION_PRESETS, get_available_presets


def _to_ass_color(col, default):
    if not col:
        return default
    c = str(col).strip()
    if c.startswith("#"):
        c = c.lstrip("#")
        if len(c) == 6:
            r, g, b = c[0:2], c[2:4], c[4:6]
            return f"&H00{b}{g}{r}".upper()
        elif len(c) == 8:
            r, g, b, a = c[0:2], c[2:4], c[4:6], c[6:8]
            return f"&H{a}{b}{g}{r}".upper()
    return c


def generate_karaoke_captions(
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
    bold: Optional[bool] = None,
    italic: Optional[bool] = None,
    uppercase: Optional[bool] = None,
    position: Optional[int] = None,  # ASS Alignment 1-9 (2 = bottom-center, 8 = top-center...)
    intro_caption: Optional[str] = None,
    intro_caption_duration: float = 3.0,
    intro_font_size: Optional[int] = None,
) -> str:
    """
    Generates an animated karaoke subtitle file (Advanced SubStation Alpha)
    with word-level \\k tags for active-word highlighting.
    Supports 20+ preset styles with individual property overrides.
    """
    preset = CAPTION_PRESETS.get(style_preset, CAPTION_PRESETS["viral_yellow"])

    f_name = font_name or preset.get("font_name", "Arial Black")
    f_size = font_size or preset.get("font_size", 26)
    p_color = _to_ass_color(primary_color, preset.get("primary_color", "&H00FFFFFF"))
    h_color = _to_ass_color(highlight_color, preset.get("highlight_color", "&H0000FFFF"))
    o_color = _to_ass_color(outline_color, preset.get("outline_color", "&H00000000"))
    b_color = preset.get("back_color", "&H80000000")
    o_width = outline_width if outline_width is not None else preset.get("outline_width", 3)
    s_width = preset.get("shadow_width", 2)
    border_style = preset.get("border_style", 1)
    margin_v = preset.get("margin_v", 240)
    f_bold = -1 if (bold if bold is not None else preset.get("bold", True)) else 0
    f_italic = -1 if (italic if italic is not None else preset.get("italic", False)) else 0
    f_uppercase = uppercase if uppercase is not None else preset.get("uppercase", True)
    f_align = position if position is not None else preset.get("position", 2)

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {play_res_x}
PlayResY: {play_res_y}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{f_name},{f_size},{p_color},{h_color},{o_color},{b_color},{f_bold},{f_italic},0,0,100,100,1,0,{border_style},{o_width},{s_width},{f_align},40,40,{margin_v},1

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

    def case(word: str) -> str:
        clean = str(word).replace("{", "").replace("}", "")
        return clean.upper() if f_uppercase else clean

    event_lines = []
    if intro_caption:
        intro_text = case(intro_caption).replace("{", "").replace("}", "")
        # Pin the intro hook to the TOP-CENTER for its first few seconds, like
        # CapCut / Opus Clip — the {\an8} override places it above the regular
        # captions (which sit at the chosen position) so the two never overlap.
        intro_end = max(0.1, float(intro_caption_duration))
        # Top-center ({\an8}); optionally a bigger font via \fs so the hook can
        # stand out from the body captions (CapCut/Opus-style).
        size_tag = f"\\fs{int(intro_font_size)}" if intro_font_size else ""
        # NOTE: the Name field is set to "intro" (Dialogue: Layer,Start,End,
        # Style,Name,...). This is authored in CLIP-RELATIVE time (0..duration),
        # so the clip-offset rebasing in ffmpeg_tools._shift_subtitle_times must
        # NOT shift it — the "intro" tag is how that step recognises and skips it.
        event_lines.append(
            f"Dialogue: 0,0:00:00.00,0:00:{intro_end:05.2f},Default,intro,0,0,0,,{{\\an8{size_tag}}}{intro_text}"
        )

    for seg in segments:
        words = getattr(seg, "words", []) or []
        # Optional diarization label ("Speaker 1"). When present it prefixes the
        # FIRST caption chunk of this segment (i.e. shows once per speaker turn).
        speaker = getattr(seg, "speaker", None)
        speaker_prefix = f"{case(str(speaker))}: " if speaker else ""
        if not words:
            # Fallback if no word timestamps
            start_str = fmt_ass_time(seg.start)
            end_str = fmt_ass_time(seg.end)
            clean_text = case(seg.text).replace("{", "").replace("}", "")
            event_lines.append(f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{speaker_prefix}{clean_text}")
            continue

        # Group words into chunks of 3-5 words for fast-paced vertical shorts reading
        f_chunk = chunk_size if (chunk_size is not None and chunk_size > 0) else 3
        for i in range(0, len(words), f_chunk):
            chunk = words[i:i + f_chunk]
            chunk_start = chunk[0].start
            chunk_end = chunk[-1].end
            
            karaoke_text = ""
            for w in chunk:
                dur_cs = max(1, int(round((w.end - w.start) * 100)))
                w_text = case(getattr(w, "word", str(w)))
                karaoke_text += f"{{\\k{dur_cs}}}{w_text} "

            # Only the first chunk of the segment carries the speaker label.
            prefix = speaker_prefix if i == 0 else ""
            start_str = fmt_ass_time(chunk_start)
            end_str = fmt_ass_time(chunk_end)
            event_lines.append(f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{prefix}{karaoke_text.strip()}")

    Path(output_ass_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_ass_path, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(event_lines))

    return output_ass_path

