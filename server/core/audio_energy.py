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


def detect_action_highlights(
    audio_path: str,
    min_duration: float = 15.0,
    max_duration: float = 45.0,
    top_k: int = 6,
) -> List[ClipCandidate]:
    """
    Transcript-free highlight detection for GAMEPLAY (shooters/Warzone etc.),
    where there's little or no speech but the action is loud — gunfights,
    explosions, killstreaks. NVIDIA-Highlights-style: find the loudest sustained
    moments and cut a clip around each.

    Works purely from the audio envelope, so it needs no captions or faces.
    Returns [] if librosa/audio is unavailable.
    """
    try:
        import librosa
        import numpy as np
    except ImportError:
        return []

    try:
        y, sr = librosa.load(audio_path, sr=16000, mono=True)
    except Exception:
        return []
    if y is None or len(y) == 0:
        return []

    total = len(y) / float(sr)
    if total < min_duration:
        # Whole thing is shorter than one clip — just take it all.
        return [
            ClipCandidate(
                id="action_0", title="🎮 Highlight", start_time=0.0, end_time=round(total, 2),
                duration=round(total, 2), score=7.0, hook_text="Gameplay highlight",
                full_text="", words=[], reason="Full gameplay segment",
            )
        ]

    hop = int(sr * 0.5)
    rms = librosa.feature.rms(y=y, hop_length=hop)[0]
    times = librosa.times_like(rms, sr=sr, hop_length=hop)
    if rms.max() > 0:
        rms = rms / rms.max()

    # "Action" = energy well above the clip's own baseline.
    mean, std = float(rms.mean()), float(rms.std())
    threshold = min(0.9, mean + 0.8 * std)

    # Peak indices, loudest first.
    peak_order = sorted(range(len(rms)), key=lambda k: rms[k], reverse=True)

    titles = ["🔥 Big Play", "💥 Intense Moment", "🎯 Killstreak", "⚡ Action Spike", "🎮 Highlight", "🏆 Clutch"]
    picked: List[ClipCandidate] = []
    windows: List = []  # (start, end)

    for k in peak_order:
        if rms[k] < threshold:
            break
        t = float(times[k])
        # Center a min-duration window on the peak, with a little more lead-out.
        half = min_duration / 2.0
        start = max(0.0, t - half * 0.8)
        end = min(total, start + min_duration)
        # Extend while the surrounding audio stays hot (up to max_duration).
        j = k + 1
        while (end - start) < max_duration and j < len(rms) and rms[j] >= threshold * 0.7:
            end = min(total, float(times[j]))
            j += 1
        # Guarantee at least min_duration (pull the start back, or push end out)
        # so a peak near the tail can't yield a stub clip.
        if (end - start) < min_duration:
            start = max(0.0, end - min_duration)
        if (end - start) < min_duration:
            end = min(total, start + min_duration)

        if any(not (end <= ws or start >= we) for ws, we in windows):
            continue  # overlaps an already-picked highlight
        windows.append((start, end))

        score = round(min(10.0, 5.0 + float(rms[k]) * 5.0), 1)
        picked.append(
            ClipCandidate(
                id=f"action_{len(picked)}",
                title=titles[len(picked) % len(titles)],
                start_time=round(start, 2),
                end_time=round(end, 2),
                duration=round(end - start, 2),
                score=score,
                hook_text="Gameplay action moment",
                full_text="",
                words=[],
                reason=f"Audio action peak ({rms[k]:.2f})",
            )
        )
        if len(picked) >= top_k:
            break

    picked.sort(key=lambda c: c.start_time)
    return picked


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