"""
Audio energy highlight detection (separate module).
Finds loudness spikes = excitement peaks, mapped onto transcript segments.
"""

import os
from typing import List, Optional

from server.models import ClipCandidate, TranscriptSegment, ViralityBreakdown, WordTimestamp
from server.core.highlight_detector import choose_hook_and_title


def _flow_from_duration(duration: float) -> float:
    """Pacing score from clip length alone: shorts land best around ~35s, and
    this is genuinely all the duration tells us. Same curve the keyword
    detector uses, kept here so both report flow on one comparable scale."""
    return round(min(10.0, max(6.0, 10.0 - abs(float(duration) - 35.0) * 0.15)), 1)


def _trend_from_score(score: float) -> str:
    """Bucket an overall 0-10 score into the card's Trend label."""
    return "Very High" if score >= 8.5 else ("High" if score >= 7.0 else "Good")


def detect_highlights_audio_energy(
    audio_path: str,
    segments: List[TranscriptSegment],
    min_duration: float = 20.0,
    max_duration: float = 60.0,
    max_speech_gap: Optional[float] = None,
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
        # librosa falls back from PySoundFile to audioread for some containers,
        # emitting a UserWarning + a FutureWarning. Both are harmless here and
        # just clutter the server log, so silence them for this load only.
        import warnings
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=UserWarning)
            warnings.filterwarnings("ignore", category=FutureWarning)
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
            if max_speech_gap is not None:
                can_lo = can_lo and segments[lo].start - segments[lo-1].end <= max_speech_gap
                can_hi = can_hi and segments[hi+1].start - segments[hi].end <= max_speech_gap
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
                # Report only what this detector actually measured. It finds
                # loudness peaks, so it can speak to energy and pacing but has
                # NOT analysed hook wording — hook_score stays None (the card
                # renders it as "–") rather than inventing a number.
                virality=ViralityBreakdown(
                    flow_score=_flow_from_duration(duration),
                    engagement_score=round(score, 1),
                    trend_potential=_trend_from_score(score),
                ),
            )
        )
    return _deduplicate(candidates)


def _motion_envelope(video_path, times):
    """
    Per-timestamp visual motion (0..1) aligned to `times`, via frame differencing
    on tiny grayscale frames. High motion = camera swings, explosions, fights.
    Returns None if OpenCV/video is unavailable. Samples are capped so long
    videos stay fast, then interpolated onto the full time grid.
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None
    if not video_path or not os.path.exists(video_path):
        return None
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return None
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    if w <= 0:
        cap.release()
        return None

    n = len(times)
    max_samples = 400
    idxs = list(range(n)) if n <= max_samples else [int(i * n / max_samples) for i in range(max_samples)]
    sample_times = [float(times[i]) for i in idxs]

    prev = None
    motion = []
    for t in sample_times:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps))
        ret, frame = cap.read()
        if not ret:
            motion.append(0.0)
            continue
        try:
            small = cv2.cvtColor(cv2.resize(frame, (160, 90)), cv2.COLOR_BGR2GRAY)
        except Exception:
            motion.append(0.0)
            continue
        if prev is None:
            motion.append(0.0)
        else:
            motion.append(float(np.mean(np.abs(small.astype("int16") - prev.astype("int16")))))
        prev = small
    cap.release()

    try:
        import numpy as np
        env = np.interp([float(t) for t in times], sample_times, motion)
        if env.max() > 0:
            env = env / env.max()
        return env
    except Exception:
        return None


def detect_action_highlights(
    audio_path: str,
    min_duration: float = 15.0,
    max_duration: float = 45.0,
    top_k: Optional[int] = 6,
    video_path: str = None,
) -> List[ClipCandidate]:
    """
    Transcript-free highlight detection for GAMEPLAY (shooters/Warzone etc.),
    where there's little or no speech but the action is loud AND busy — gunfights,
    explosions, killstreaks. NVIDIA-Highlights-style: find the most intense
    sustained moments and cut a clip around each.

    Fuses two local signals: audio loudness (gunfire/explosions) and, when a
    video path is given, visual motion (frame differencing). Loud + high-motion
    scores highest. Falls back to audio-only if OpenCV can't read the video.
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

    if top_k is not None and top_k <= 0:
        return []
    total = len(y) / float(sr)
    hop = int(sr * 0.5)
    rms = librosa.feature.rms(y=y, hop_length=hop)[0]
    times = librosa.times_like(rms, sr=sr, hop_length=hop)
    if rms.max() > 0:
        rms = rms / rms.max()

    # Fuse audio loudness with visual motion when the video is available.
    motion = _motion_envelope(video_path, times) if video_path else None
    if motion is not None and len(motion) == len(rms):
        signal = 0.6 * rms + 0.4 * motion
        if signal.max() > 0:
            signal = signal / signal.max()
    else:
        signal = rms

    if not np.any(signal > 0):
        return []

    # "Action" = intensity well above the clip's own baseline.
    mean, std = float(signal.mean()), float(signal.std())
    # A peak must rise meaningfully above this recording's baseline. Capping
    # the threshold below 1 made uniform noise qualify after normalization.
    threshold = mean + max(0.8 * std, 0.15)
    if float(signal.max()) <= threshold:
        return []

    # Peak indices, most-intense first.
    peak_order = sorted(range(len(signal)), key=lambda k: signal[k], reverse=True)

    titles = ["Action highlight", "High-energy moment", "Motion highlight", "Action peak"]
    picked: List[ClipCandidate] = []
    windows: List = []  # (start, end)

    for k in peak_order:
        if signal[k] < threshold:
            break
        t = float(times[k])
        # Center a min-duration window on the peak, with a little more lead-out.
        half = min_duration / 2.0
        start = max(0.0, t - half * 0.8)
        end = min(total, start + min_duration)
        # Extend while the surrounding audio stays hot (up to max_duration).
        j = k + 1
        while (end - start) < max_duration and j < len(signal) and signal[j] >= threshold * 0.7:
            end = max(end, min(total, start + max_duration, float(times[j])))
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

        score = round(min(10.0, 5.0 + float(signal[k]) * 5.0), 1)
        sig_kind = "audio+motion" if motion is not None else "audio"
        picked.append(
            ClipCandidate(
                id=f"action_{len(picked)}",
                title=f"{titles[len(picked) % len(titles)]} at {int(t) // 60}:{int(t) % 60:02d}",
                start_time=round(start, 2),
                end_time=round(end, 2),
                duration=round(end - start, 2),
                score=score,
                hook_text="Audio / motion peak",
                description="Selected for an audio or motion peak. Review the footage to confirm the moment.",
                full_text="",
                words=[],
                reason=f"Action peak ({sig_kind} {signal[k]:.2f})",
                # Transcript-free detector: it fuses loudness + visual motion,
                # so there is no hook wording to score at all.
                virality=ViralityBreakdown(
                    flow_score=_flow_from_duration(end - start),
                    engagement_score=score,
                    trend_potential=_trend_from_score(score),
                ),
            )
        )
        if top_k is not None and len(picked) >= top_k:
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