"""
Filler-word + dead-air removal.

Uses the Whisper word timestamps we already produce to cut disfluencies
("um", "uh", "er", ...) and, optionally, dead-air silence — tightening a clip
without any new dependency. Conservative defaults so real speech isn't butchered.
"""

import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

from server.core.ffmpeg_tools import get_video_duration
from server.core.silence_cutter import (
    calculate_speech_segments,
    detect_silence_intervals,
    render_kept_segments,
)

# Conservative, high-confidence disfluencies. Deliberately excludes ambiguous
# words like "like"/"so"/"right" that are usually meaningful; callers can add
# extras explicitly if they want a more aggressive cut.
DEFAULT_FILLERS = {"um", "uh", "erm", "uhm", "hmm", "mm", "mmm", "eh", "ah", "er"}


def _clean_token(word: str) -> str:
    return re.sub(r"[^a-z]", "", (word or "").lower())


def _merge_intervals(intervals: List[Dict[str, float]]) -> List[Dict[str, float]]:
    """Sort + merge overlapping/adjacent cut intervals."""
    if not intervals:
        return []
    ivs = sorted(intervals, key=lambda x: x["start"])
    merged = [dict(ivs[0])]
    for iv in ivs[1:]:
        if iv["start"] <= merged[-1]["end"] + 0.05:
            merged[-1]["end"] = max(merged[-1]["end"], iv["end"])
        else:
            merged.append(dict(iv))
    for m in merged:
        m["duration"] = round(m["end"] - m["start"], 3)
    return merged


def remove_fillers(
    input_video: str,
    output_video: str,
    words: List[Dict[str, Any]],
    extra_fillers: List[str] = None,
    also_remove_silence: bool = True,
    pad_seconds: float = 0.04,
) -> Dict[str, Any]:
    """Cut filler words (and optionally dead air) from a clip.

    words: [{"word","start","end"}, ...] for THIS clip (clip-local timestamps).
    Returns metadata incl. output_path, durations, and how much was trimmed.
    """
    orig_dur = get_video_duration(input_video)
    if orig_dur <= 0:
        raise ValueError(f"Invalid duration for video: {input_video}")

    fillers = set(DEFAULT_FILLERS)
    for f in (extra_fillers or []):
        c = _clean_token(f)
        if c:
            fillers.add(c)

    # Build cut intervals from filler words (with a hair of padding).
    cuts: List[Dict[str, float]] = []
    n_filler = 0
    for w in words or []:
        if _clean_token(w.get("word", "")) in fillers:
            s = float(w.get("start", 0)) - pad_seconds
            e = float(w.get("end", 0)) + pad_seconds
            if e > s:
                cuts.append({"start": max(0.0, s), "end": min(orig_dur, e)})
                n_filler += 1

    if also_remove_silence:
        try:
            cuts.extend(detect_silence_intervals(input_video))
        except Exception:
            pass

    cuts = _merge_intervals(cuts)
    if not cuts:
        import shutil
        Path(output_video).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(input_video, output_video)
        return {
            "output_path": output_video,
            "original_duration": round(orig_dur, 2),
            "cut_duration": round(orig_dur, 2),
            "time_saved": 0.0,
            "fillers_removed": 0,
            "message": "No fillers or dead air found to remove.",
        }

    keep = calculate_speech_segments(orig_dur, cuts, pad_seconds=0.0)
    render_kept_segments(input_video, output_video, keep)

    cut_dur = get_video_duration(output_video)
    return {
        "output_path": output_video,
        "original_duration": round(orig_dur, 2),
        "cut_duration": round(cut_dur, 2),
        "time_saved": max(0.0, round(orig_dur - cut_dur, 2)),
        "fillers_removed": n_filler,
        "message": f"Removed {n_filler} filler word(s)"
                   + (" + dead air" if also_remove_silence else "")
                   + f", saved {max(0.0, round(orig_dur - cut_dur, 2))}s.",
    }
