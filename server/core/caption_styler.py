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
    intro_style: Optional[dict] = None,
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
        opts = intro_style or {}
        ip = CAPTION_PRESETS.get(opts.get("preset"), preset)
        def iv(key, default):
            return opts.get(key) if opts.get(key) not in (None, "") else ip.get(key, default)
        name = str(iv("font_name", f_name)).replace(",", "").replace("\n", "")
        color = _to_ass_color(iv("primary_color", p_color), p_color)
        outline = _to_ass_color(iv("outline_color", o_color), o_color)
        width = max(0, min(12, float(iv("outline_width", o_width))))
        align = int(opts.get("position") or 8)
        if align not in (2, 5, 8): align = 8
        size = max(8, min(200, int(intro_font_size or 64)))
        ib = -1 if iv("bold", True) else 0
        ii = -1 if iv("italic", False) else 0
        style_line = f"Style: Intro,{name},{size},{color},{color},{outline},{b_color},{ib},{ii},0,0,100,100,1,0,1,{width},2,{align},40,40,180,1\n"
        header = header.replace("[Events]", style_line + "\n[Events]")
        intro_text = str(intro_caption).replace("{", "").replace("}", "").replace(chr(92), "").replace("\n", " ")
        if iv("uppercase", False): intro_text = intro_text.upper()
        intro_end = max(0.1, float(intro_caption_duration))
        animation = {"fade": r"\fad(200,150)", "pop": r"\fscx80\fscy80\t(0,180,\fscx100\fscy100)"}.get(opts.get("animation"), "")
        # The intro name keeps this event clip-relative during subtitle rebasing.
        alignment_tag = chr(92) + "an" + str(align)
        if opts.get("box"):
            opacity = max(0, min(100, float(opts.get("box_opacity", 100))))
            padding = max(0, min(40, float(opts.get("box_padding", 12))))
            box_color = _to_ass_color(opts.get("box_color", "#111111"), "&H00111111")
            box_color = f"&H{round(255 * (1-opacity/100)):02X}" + box_color[-6:]
            # BorderStyle 3 draws a square opaque box around the same text metrics.
            # A separate layer retains the headline's independent text outline.
            box_style = f"Style: IntroBox,{name},{size},&HFF000000,&HFF000000,{box_color},{box_color},{ib},{ii},0,0,100,100,1,0,3,{padding},0,{align},40,40,180,1\n"
            header = header.replace("[Events]", box_style + "\n[Events]")
            event_lines.append(f"Dialogue: 0,0:00:00.00,{fmt_ass_time(intro_end)},IntroBox,intro,0,0,0,,{{{alignment_tag}{animation}}}{intro_text}")
        text_layer = 1 if opts.get("box") else 0
        event_lines.append(f"Dialogue: {text_layer},0:00:00.00,{fmt_ass_time(intro_end)},Intro,intro,0,0,0,,{{{alignment_tag}{animation}}}{intro_text}")

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
            n_chunk = len(chunk)
            chunk_words = [case(getattr(w, "word", str(w))) for w in chunk]

            # Emit one Dialogue event PER WORD so that only the word currently
            # being spoken is shown in the accent (highlight) color, with the
            # rest of the chunk in the base color — a timing-accurate "active
            # word" highlight (CapCut/Opus style). Progressive \k karaoke put
            # the accent on the *upcoming* word instead of the current one and
            # drifted out of sync across pauses; keying each word's own line to
            # [word.start, next_word.start] locks the highlight to the audio and
            # holds it through any inter-word gap.
            for k, w in enumerate(chunk):
                seg_start = float(getattr(w, "start", 0.0) or 0.0)
                if k < n_chunk - 1:
                    seg_end = float(getattr(chunk[k + 1], "start", seg_start) or seg_start)
                else:
                    seg_end = float(getattr(w, "end", seg_start) or seg_start)
                if seg_end <= seg_start:
                    seg_end = seg_start + 0.04  # guarantee at least one visible frame

                # Colour only the active word; {\r} resets the rest to the style
                # default (base colour, style bold/italic preserved).
                rendered = []
                for j, wt in enumerate(chunk_words):
                    if j == k:
                        rendered.append(f"{{\\c{h_color}&}}{wt}{{\\r}}")
                    else:
                        rendered.append(wt)
                line_text = " ".join(rendered)

                # The speaker label stays on the segment's first chunk.
                prefix = speaker_prefix if i == 0 else ""
                start_str = fmt_ass_time(seg_start)
                end_str = fmt_ass_time(seg_end)
                event_lines.append(f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{prefix}{line_text}")

    Path(output_ass_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_ass_path, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(event_lines))

    return output_ass_path

