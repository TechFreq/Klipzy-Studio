"""
Audio energy highlight detection (separate module).
Finds loudness spikes = excitement peaks, mapped onto transcript segments.
"""

from typing import List

from server.models import ClipCandidate, TranscriptSegment, WordTimestamp
from server.core.highlight_detector import choose_hook_and_title


def detect_highlights_audio_energy(
    audio_path: str,
    segments: List[TranscriptSegment],
    min_duration: float = 20.0,
    max_duration: float = 60.0,
) -> List[ClipCandidate]:
    """
    Uses librosa to find high-energy audio windows (excitement/loudness spikes),
    then EXPANDS each peak into a proper clip window that respects min/max
    duration — a loud one-word segment ("Download Mike!") must not become a
    0.7s "clip". Peaks are grown outward over neighbouring segments (following
    the louder side) until they reach min_duration, capped at max_duration.

    The audio is a mono 16kHz WAV extracted by the pipeline. Returns [] if
    segments/librosa is unavailable.
    """
    try:
        import librosa
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

    # Per-segment mean energy.
    energies: List[float] = []
    for seg in segments:
        mask = (times >= seg.start) & (times <= seg.end)
        energies.append(float(rms[mask].mean()) if mask.any() else 0.0)

    n = len(segments)
    candidates: List[ClipCandidate] = []
    used = set()

    # Visit segments loudest-first so the strongest peaks anchor windows.
    for i in sorted(range(n), key=lambda k: energies[k], reverse=True):
        if energies[i] <= 0.6 or i in used:
            continue

        lo = hi = i
        start, end = segments[lo].start, segments[hi].end
        # Grow outward toward the louder neighbour until we hit min_duration.
        while (end - start) < min_duration:
            can_lo = lo > 0 and (lo - 1) not in used
            can_hi = hi < n - 1 and (hi + 1) not in used
            if not can_lo and not can_hi:
                break
            grow_hi = can_hi and (not can_lo or energies[hi + 1] >= energies[lo - 1])
            if grow_hi:
                if (segments[hi + 1].end - start) > max_duration:
                    break
                hi += 1
                end = segments[hi].end
            else:
                if (end - segments[lo - 1].start) > max_duration:
                    break
                lo -= 1
                start = segments[lo].start

        duration = end - start
        if duration < min(min_duration, 5.0):  # still too short after growing → skip
            continue

        for k in range(lo, hi + 1):
            used.add(k)

        text = " ".join(s.text for s in segments[lo:hi + 1]).strip()
        words: List[WordTimestamp] = [w for s in segments[lo:hi + 1] for w in (s.words or [])]
        peak = energies[i]
        score = min(10.0, 5.0 + peak * 5.0)
        hook_text, title = choose_hook_and_title(text, len(candidates) + 1)

        candidates.append(
            ClipCandidate(
                id=f"energy_{i}",
                title=title,
                start_time=round(start, 2),
                end_time=round(end, 2),
                duration=round(duration, 2),
                score=round(score, 1),
                hook_text=hook_text,
                full_text=text,
                words=words,
                reason=f"High audio energy ({peak:.2f})",
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