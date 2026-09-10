"""
Overlay Manager - B-roll overlays, visual stickers, and transcript emoji injection.
Supports image overlays (PNG/JPG/WebP), B-roll video cutaways, and keyword-based emoji punch-ups.
"""

import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import subprocess  # kept for subprocess.PIPE

from server.core.ffmpeg_tools import detect_hw_encoder, X264_FALLBACK_ARGS
from server.core import proc  # killable subprocess runner (job cancel)
from server.models import TranscriptSegment, WordTimestamp

# High-virality keyword-to-emoji mappings for TikTok / Shorts / Reels style punch-ups
KEYWORD_EMOJIS: Dict[str, str] = {
    "money": "💸", "cash": "💰", "dollar": "💵", "rich": "🤑", "wealth": "📈",
    "crazy": "🤯", "insane": "😱", "shocking": "⚡", "wild": "🔥",
    "love": "❤️", "hate": "💔", "heart": "💖", "best": "⭐",
    "win": "🏆", "winner": "🥇", "success": "🚀", "goal": "🎯",
    "stop": "🛑", "warning": "⚠️", "danger": "🚨", "secret": "🤫",
    "idea": "💡", "think": "🧠", "smart": "🧐", "truth": "🔍",
    "fire": "🔥", "hot": "🌶️", "power": "⚡", "strong": "💪",
    "food": "🍔", "coffee": "☕", "drink": "🥤", "music": "🎵",
    "laugh": "😂", "funny": "🤣", "joke": "🎭", "cry": "😭",
    "game": "🎮", "gaming": "👾", "code": "💻", "tech": "🤖",
    "ai": "🤖", "future": "🔮", "magic": "✨", "time": "⏳",
    "fast": "⚡", "slow": "🐢", "car": "🏎️", "travel": "✈️",
}


def suggest_emojis_for_segments(segments: List[TranscriptSegment]) -> List[Dict]:
    """
    Scans transcript segments for high-energy hook keywords and returns suggested emoji placements:
    [{"word": str, "emoji": str, "timestamp": float, "start": float, "end": float}]
    """
    suggestions = []
    for seg in segments:
        words = seg.words if getattr(seg, "words", None) else []
        if words:
            for w in words:
                clean = re.sub(r"[^A-Za-z]+", "", w.word).lower()
                if clean in KEYWORD_EMOJIS:
                    suggestions.append({
                        "word": w.word,
                        "emoji": KEYWORD_EMOJIS[clean],
                        "timestamp": round(w.start, 2),
                        "start": round(w.start, 2),
                        "end": round(w.end, 2),
                    })
        else:
            # Fallback to segment text splitting
            raw_words = seg.text.split()
            for rw in raw_words:
                clean = re.sub(r"[^A-Za-z]+", "", rw).lower()
                if clean in KEYWORD_EMOJIS:
                    suggestions.append({
                        "word": rw,
                        "emoji": KEYWORD_EMOJIS[clean],
                        "timestamp": round(seg.start, 2),
                        "start": round(seg.start, 2),
                        "end": round(seg.end, 2),
                    })
    return suggestions


def inject_emojis_into_transcript(segments: List[TranscriptSegment]) -> List[TranscriptSegment]:
    """
    Injects contextual emojis directly alongside matched keywords in the transcript segment text.
    """
    for seg in segments:
        words_list = getattr(seg, "words", [])
        if words_list:
            for w in words_list:
                clean = re.sub(r"[^A-Za-z]+", "", w.word).lower()
                if clean in KEYWORD_EMOJIS and KEYWORD_EMOJIS[clean] not in w.word:
                    w.word = f"{w.word} {KEYWORD_EMOJIS[clean]}"
            seg.text = " ".join(w.word for w in words_list)
        else:
            tokens = seg.text.split()
            new_tokens = []
            for t in tokens:
                clean = re.sub(r"[^A-Za-z]+", "", t).lower()
                if clean in KEYWORD_EMOJIS:
                    new_tokens.append(f"{t} {KEYWORD_EMOJIS[clean]}")
                else:
                    new_tokens.append(t)
            seg.text = " ".join(new_tokens)
    return segments


def apply_broll_overlay(
    input_video: str,
    output_video: str,
    broll_path: str,
    start_time: float,
    duration: float,
    scale: float = 0.9,
    position: str = "center",
    opacity: float = 1.0,
    subtitle_path: Optional[str] = None,
) -> str:
    """
    Overlays an image or B-roll video clip on top of input_video during [start_time, start_time + duration].
    Optionally burns animated karaoke captions simultaneously in the same FFmpeg encode pass.
    Position: 'center', 'top-right', 'top-left', 'bottom-right', 'bottom-left'.
    """
    if not os.path.exists(input_video):
        raise FileNotFoundError(f"Input video not found: {input_video}")
    if not os.path.exists(broll_path):
        raise FileNotFoundError(f"B-roll asset not found: {broll_path}")

    Path(output_video).parent.mkdir(parents=True, exist_ok=True)
    encoder, enc_args = detect_hw_encoder()

    pos_expr = {
        "center": "(W-w)/2:(H-h)/2",
        "top-left": "20:20",
        "top-right": "W-w-20:20",
        "bottom-left": "20:H-h-20",
        "bottom-right": "W-w-20:H-h-20",
    }.get(position, "(W-w)/2:(H-h)/2")

    end_time = start_time + duration

    burn_cwd = None
    if subtitle_path and os.path.exists(subtitle_path):
        # On Windows, ffmpeg's subtitles filter cannot parse a drive-letter path cleanly.
        # Run with cwd = subtitle dir and use relative path.
        from server.core.ffmpeg_tools import _shift_subtitle_times
        subtitle_to_burn = _shift_subtitle_times(subtitle_path, 0.0)
        sub_dir = os.path.dirname(os.path.abspath(subtitle_to_burn))
        sub_rel = os.path.basename(subtitle_to_burn).replace("'", "'\\''")
        burn_cwd = sub_dir
        filter_complex = (
            f"[1:v]scale=iw*{scale}:-1,format=yuva420p,colorchannelmixer=aa={opacity}[broll];"
            f"[0:v][broll]overlay={pos_expr}:enable='between(t,{start_time},{end_time})'[v_overlay];"
            f"[v_overlay]subtitles={sub_rel},null[out]"
        )
    else:
        filter_complex = (
            f"[1:v]scale=iw*{scale}:-1,format=yuva420p,colorchannelmixer=aa={opacity}[broll];"
            f"[0:v][broll]overlay={pos_expr}:enable='between(t,{start_time},{end_time})'[out]"
        )

    cmd = [
        "ffmpeg", "-y",
        "-i", input_video,
        "-i", broll_path,
        "-filter_complex", filter_complex,
        "-map", "[out]", "-map", "0:a:0?",
        "-c:v", encoder,
        *enc_args,
        "-c:a", "aac", "-b:a", "192k",
        output_video,
    ]

    res = proc.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=burn_cwd)
    if res.returncode != 0:
        # Fallback to libx264
        cmd_fb = [c for c in cmd]
        enc_i = cmd_fb.index("-c:v")
        enc_args_i = enc_i + 2
        cmd_fb[enc_i + 1] = "libx264"
        cmd_fb[enc_args_i:enc_args_i + len(enc_args)] = X264_FALLBACK_ARGS
        res_fb = proc.run(cmd_fb, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=burn_cwd)
        if res_fb.returncode != 0:
            raise RuntimeError(f"B-roll overlay failed: {res_fb.stderr[-500:]}")

    return output_video
