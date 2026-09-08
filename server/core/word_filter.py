"""
Word-level profanity & custom word bleep / mute filter.
Censors specific timestamps with 1000Hz bleep tone or audio muting,
and optionally sanitizes transcript/subtitle text.
"""

import re
import os
import subprocess
from pathlib import Path
from typing import List, Dict, Tuple, Any, Optional, Set
from server.core.ffmpeg_tools import detect_hw_encoder, get_video_duration, X264_FALLBACK_ARGS


DEFAULT_PROFANITY_LIST: Set[str] = {
    "fuck", "fucking", "fucked", "fucker", "fuckers", "motherfucker", "motherfucking",
    "shit", "shits", "bullshit", "shitty", "bitch", "bitches", "bitching",
    "cunt", "cunts", "asshole", "assholes", "dick", "dicks", "cock", "cocks",
    "bastard", "bastards", "damn", "damned", "goddamn", "goddamned", "pussy", "pussies",
    "whore", "whores", "slut", "sluts", "nigger", "niggers", "nigga", "niggas",
    "fag", "faggot", "faggots", "prick", "pricks", "twat", "twats", "wanker",
    "wankers", "bollocks", "arse", "arsehole", "arseholes", "dickhead", "dickheads",
    "cocksucker", "cocksuckers", "dumbass", "dumbasses", "jackass", "dipshit",
    "douchebag", "douchebags",
}


# Stems safe to match as substrings so compounds like "goddamn" /
# "motherfucking" / "bullshit" are still caught, WITHOUT the false positives a
# blanket substring scan produced (e.g. "cock" in "peacock", "dick" in
# "Dickens", "ass" in "class"/"pass", "damn" in "condemn"). Only stems that
# essentially never appear inside innocent English words belong here.
PROFANITY_SUBSTRING_STEMS: Set[str] = {
    "fuck", "shit", "bitch", "cunt", "motherfuck", "faggot", "nigger", "nigga",
}


def _clean_token(word: Any) -> str:
    """Lower-case a token and strip surrounding punctuation for matching."""
    return re.sub(r"[^\w]", "", str(word)).lower()


def _is_profane(clean: str, words_to_censor: Set[str]) -> bool:
    """True when a cleaned token is an exact profanity/custom word, or contains
    one of the unambiguous substring stems. Exact-match first keeps precision
    high; the narrow stem list catches compounds without over-censoring."""
    if not clean:
        return False
    if clean in words_to_censor:
        return True
    return any(stem in clean for stem in PROFANITY_SUBSTRING_STEMS)


def _censor_set(custom_words: Optional[List[str]]) -> Set[str]:
    words_to_censor = set(DEFAULT_PROFANITY_LIST)
    for w in custom_words or []:
        cleaned = str(w).lower().strip()
        if cleaned:
            words_to_censor.add(cleaned)
    return words_to_censor


def sanitize_text(text: str, custom_words: Optional[List[str]] = None) -> str:
    """Replaces profanities with asterisks (e.g. 'f**k', 's**t')."""
    words_to_censor = _censor_set(custom_words)

    def replace_match(match):
        w = match.group(0)
        if _is_profane(_clean_token(w), words_to_censor):
            if len(w) <= 2:
                return "*" * len(w)
            return w[0] + ("*" * (len(w) - 2)) + w[-1]
        return w

    return re.sub(r"\b[A-Za-z0-9_']+\b", replace_match, text)


def find_profanity_timestamps(
    segments: List[Any],
    custom_words: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Scans word-level timestamps in transcript segments to locate profanities.
    Returns list of dicts: [{"word": "fuck", "start": 3.42, "end": 3.85}, ...]
    """
    words_to_censor = _censor_set(custom_words)

    matches = []
    for seg in segments:
        words = getattr(seg, "words", []) or []
        for w in words:
            clean = _clean_token(getattr(w, "word", str(w)))
            if _is_profane(clean, words_to_censor):
                matches.append({
                    "word": clean,
                    "start": round(float(w.start), 3),
                    "end": round(float(w.end), 3),
                    "duration": round(float(w.end - w.start), 3),
                })

    return matches


def filter_word_timestamps(
    words: List[Dict[str, Any]],
    custom_words: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Keep only the profane entries from a list of ``{word, start, end}`` dicts.

    This is what the /tools/bleep-mute quick action uses: the UI sends every
    word of the clip, and we narrow it down to just the profanity here so the
    bleep lands on the right words instead of the entire clip.
    """
    words_to_censor = _censor_set(custom_words)
    out: List[Dict[str, Any]] = []
    for w in words or []:
        if not isinstance(w, dict):
            continue
        clean = _clean_token(w.get("word", ""))
        if not _is_profane(clean, words_to_censor):
            continue
        try:
            start = float(w.get("start"))
            end = float(w.get("end"))
        except (TypeError, ValueError):
            continue
        out.append({
            "word": clean,
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(max(0.0, end - start), 3),
        })
    return out


def apply_bleep_or_mute(
    input_video: str,
    output_video: str,
    timestamps: List[Dict[str, float]],
    mode: str = "bleep",  # "bleep" | "mute"
    beep_freq: int = 1000,
) -> Dict[str, Any]:
    """
    Applies audio censoring at specified timestamps.
    - 'mute': Ducks audio volume to 0 during target intervals.
    - 'bleep': Ducks original audio and overlays a standard 1000Hz censor tone.
    """
    if not os.path.exists(input_video):
        raise FileNotFoundError(f"Input video not found: {input_video}")

    if not timestamps:
        # Nothing to bleep, copy cleanly
        Path(output_video).parent.mkdir(parents=True, exist_ok=True)
        import shutil
        shutil.copy2(input_video, output_video)
        return {
            "output_path": output_video,
            "censored_count": 0,
            "mode": mode,
            "message": "No profanity timestamps to censor.",
        }

    encoder, enc_args = detect_hw_encoder()
    dur = get_video_duration(input_video)

    # Build volume expression for muting original audio during words
    mute_conditions = [f"between(t,{t['start']},{t['end']})" for t in timestamps]
    vol_eval = "+".join(mute_conditions)
    # volume is 0 if any condition is active, else 1
    vol_filter = f"volume=enable='{vol_eval}':volume=0"

    Path(output_video).parent.mkdir(parents=True, exist_ok=True)

    if mode == "mute":
        filter_complex = f"[0:a]{vol_filter}[aout]"
        cmd = [
            "ffmpeg", "-y",
            "-i", input_video,
            "-filter_complex", filter_complex,
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            output_video
        ]
    else:
        # Bleep mode: generate sine tone and mix with muted audio
        # Generate tone with volume matching target intervals
        tone_filter = f"sine=frequency={beep_freq}:duration={dur},volume=enable='{vol_eval}':volume=0.3[beep]"
        filter_complex = (
            f"[0:a]{vol_filter}[muted];"
            f"{tone_filter};"
            f"[muted][beep]amix=inputs=2:duration=first:dropout_transition=0[aout]"
        )
        cmd = [
            "ffmpeg", "-y",
            "-i", input_video,
            "-filter_complex", filter_complex,
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            output_video
        ]

    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        # If copy fails or filter errors, try re-encoding video
        cmd[cmd.index("-c:v") + 1] = encoder
        res_fb = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res_fb.returncode != 0:
            raise RuntimeError(f"Profanity bleep/mute failed: {res_fb.stderr[-500:]}")

    return {
        "output_path": output_video,
        "censored_count": len(timestamps),
        "mode": mode,
        "timestamps": timestamps,
        "message": f"Successfully applied {mode} filter to {len(timestamps)} detected words.",
    }
