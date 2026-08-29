"""
Audio energy highlight detection (separate module).
Finds loudness spikes = excitement peaks, mapped onto transcript segments.
"""

from typing import List

from server.models import ClipCandidate, TranscriptSegment


def detect_highlights_audio_energy(audio_path: str, segments: List[TranscriptSegment]) -> List[ClipCandidate]:
    """
    Uses librosa (or numpy fallback) to find high-energy audio windows,
    then maps them onto transcript segments.

    The audio is a mono 16kHz WAV extracted by the pipeline, so loading the
    full file is fine for typical podcasts/streams; librosa streams internally.
    Returns [] if segments/librosa is unavailable.
    """
    try:
        import librosa
        import numpy as np
    except ImportError:
        return []

    if not segments:
        return []

    try:
        y, sr = librosa.load(audio_path, sr=16000, mono=True)
        hop = int(sr * 0.5)
        rms = librosa.feature.rms(y=y, hop_length=hop)[0]
        times = librosa.times_like(rms, sr=sr, hop_length=hop)
    except Exception:
        return []

    if rms.max() > 0:
        rms = rms / rms.max()

    candidates: List[ClipCandidate] = []
    for i, seg in enumerate(segments):
        mask = (times >= seg.start) & (times <= seg.end)
        if not mask.any():
            continue
        seg_energy = float(rms[mask].mean())
        if seg_energy > 0.6:
            score = min(10.0, 5.0 + seg_energy * 5.0)
            candidates.append(
                ClipCandidate(
                    id=f"energy_{i}",
                    title=(seg.text[:52].strip() or f"Energy Highlight {i}"),
                    start_time=seg.start,
                    end_time=seg.end,
                    duration=round(seg.end - seg.start, 2),
                    score=round(score, 1),
                    hook_text=seg.text[:60] + "...",
                    full_text=seg.text,
                    reason=f"High audio energy ({seg_energy:.2f})",
                )
            )
    return _deduplicate(candidates)


def _deduplicate(clips: List[ClipCandidate], overlap_threshold: float = 0.5) -> List[ClipCandidate]:
    selected: List[ClipCandidate] = []
    for c in clips:
        overlap = False
        for s in selected:
            overlap_start = max(c.start_time, s.start_time)
            overlap_end = min(c.end_time, s.end_time)
            if overlap_start < overlap_end:
                if (overlap_end - overlap_start) / min(c.duration, s.duration) >= overlap_threshold:
                    overlap = True
                    break
        if not overlap:
            selected.append(c)
    return selected