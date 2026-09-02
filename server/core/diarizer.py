"""
Speaker diarization ("who spoke when") — OPTIONAL.

⚠️ UNTESTED / OPTIONAL: this uses pyannote.audio, a heavy extra dependency that
also needs a Hugging Face token and a model download. It is NOT installed by
default and was written without hardware to test it on. Everything degrades
gracefully: if pyannote (or a token/model) is missing, diarization_available()
returns False and callers should hide/disable the feature rather than error.
"""

import os
from typing import Any, Dict, List, Optional


def diarization_available() -> bool:
    """True only if pyannote.audio is importable. (A working run also needs a
    HUGGINGFACE token + the pretrained pipeline downloaded.)"""
    try:
        import pyannote.audio  # noqa: F401
        return True
    except Exception:
        return False


def assign_speakers_to_segments(
    transcript_segments: List[Dict[str, Any]],
    diar_segments: List[Dict[str, Any]],
) -> List[Optional[str]]:
    """Label each transcript segment with the diarization speaker it overlaps
    most (or None). Pure + testable — the groundwork for speaker-labeled
    captions and speaker-aware selection, independent of pyannote being present.

    transcript_segments: [{"start","end",...}]  (clip/transcript timeline)
    diar_segments:        [{"start","end","speaker"}]
    """
    labels: List[Optional[str]] = []
    for seg in transcript_segments or []:
        s, e = float(seg.get("start", 0)), float(seg.get("end", 0))
        best, best_overlap = None, 0.0
        for d in diar_segments or []:
            overlap = max(0.0, min(e, float(d.get("end", 0))) - max(s, float(d.get("start", 0))))
            if overlap > best_overlap:
                best_overlap, best = overlap, d.get("speaker")
        labels.append(best)
    return labels


def build_speaker_name_map(diar_segments: List[Dict[str, Any]]) -> Dict[str, str]:
    """Map raw diarization labels ("SPEAKER_00", ...) to friendly, 1-based names
    ("Speaker 1", ...), ordered by when each speaker FIRST talks. Pure + testable.

    So whoever speaks first in the clip becomes "Speaker 1" regardless of the
    arbitrary index pyannote assigns.
    """
    order: List[str] = []
    for d in sorted(diar_segments or [], key=lambda x: float(x.get("start", 0))):
        sp = d.get("speaker")
        if sp is not None and sp not in order:
            order.append(sp)
    return {raw: f"Speaker {i + 1}" for i, raw in enumerate(order)}


def _word_field(w: Any, key: str, default: Any = None) -> Any:
    """Read a word's field whether it's a dict or an object (WordTimestamp)."""
    if isinstance(w, dict):
        return w.get(key, default)
    return getattr(w, key, default)


def group_words_into_speaker_turns(
    words: List[Any],
    diar_segments: List[Dict[str, Any]],
    diar_offset: float = 0.0,
) -> List[Dict[str, Any]]:
    """Assign each word to the diarization speaker it overlaps most, then merge
    consecutive same-speaker words into "turns". Pure + testable — this is the
    groundwork for speaker-prefixed captions, independent of pyannote.

    words:         [{"word","start","end"} ...] or WordTimestamp-like objects
                   (kept on their ORIGINAL timeline in the returned turns).
    diar_segments: [{"start","end","speaker"}] with raw pyannote labels.
    diar_offset:   seconds ADDED to each diar segment before overlap testing —
                   use it to line up a clip-local diarization with source-time
                   words (offset = clip start) or vice-versa.

    Returns turns: [{"speaker": "Speaker 1", "speaker_raw": "SPEAKER_00",
                     "start", "end", "words": [...]}]. Words that overlap no turn
    inherit the neighbouring speaker so we never emit orphan single-word turns.
    """
    words = list(words or [])
    if not words:
        return []

    shifted = [
        {
            "start": float(d.get("start", 0)) + diar_offset,
            "end": float(d.get("end", 0)) + diar_offset,
            "speaker": d.get("speaker"),
        }
        for d in (diar_segments or [])
    ]
    name_map = build_speaker_name_map(shifted)

    word_segs = [
        {"start": float(_word_field(w, "start", 0)), "end": float(_word_field(w, "end", 0))}
        for w in words
    ]
    raw_labels = assign_speakers_to_segments(word_segs, shifted)

    # Carry a speaker across un-assigned gaps: forward-fill, then back-fill any
    # leading Nones with the first known speaker.
    last: Optional[str] = None
    for i, lab in enumerate(raw_labels):
        if lab is None:
            raw_labels[i] = last
        else:
            last = lab
    first_known = next((lab for lab in raw_labels if lab is not None), None)
    raw_labels = [lab if lab is not None else first_known for lab in raw_labels]

    turns: List[Dict[str, Any]] = []
    for w, raw in zip(words, raw_labels):
        if not turns or turns[-1]["speaker_raw"] != raw:
            turns.append({
                "speaker_raw": raw,
                "speaker": name_map.get(raw) if raw is not None else None,
                "start": float(_word_field(w, "start", 0)),
                "end": float(_word_field(w, "end", 0)),
                "words": [w],
            })
        else:
            turns[-1]["words"].append(w)
            turns[-1]["end"] = float(_word_field(w, "end", 0))
    return turns


def speaker_coherence(start: float, end: float, diar_segments: List[Dict[str, Any]]) -> float:
    """Score how "clean" the speaker structure of a [start, end] window is, in
    0..1. Pure + testable. Higher = a better short-form clip candidate:

      * a single dominant speaker (monologue)              -> ~1.0
      * a clean two-person back-and-forth                  -> high
      * 3+ speakers or frantic talking-over-each-other     -> lower
      * a window that is mostly silence                    -> pulled down

    The score is intended as a gentle multiplier on an existing highlight score,
    not a hard gate — see rank_clips_by_speaker.
    """
    start, end = float(start), float(end)
    dur = max(1e-6, end - start)

    talk: Dict[Any, float] = {}
    ordered: List[Any] = []  # speaker per overlapping turn, in time order
    for d in sorted(diar_segments or [], key=lambda x: float(x.get("start", 0))):
        s = max(start, float(d.get("start", 0)))
        e = min(end, float(d.get("end", 0)))
        if e - s <= 0:
            continue
        sp = d.get("speaker")
        talk[sp] = talk.get(sp, 0.0) + (e - s)
        ordered.append(sp)

    if not talk:
        return 0.0  # no speech in the window -> not a talking-clip candidate

    total = sum(talk.values())
    n = len(talk)
    switches = sum(1 for i in range(1, len(ordered)) if ordered[i] != ordered[i - 1])
    switch_rate = switches / dur  # speaker changes per second

    if n <= 1:
        base = 1.0
    elif n == 2:
        base = 0.85
    else:
        base = 0.5  # 3+ speakers is messier for a short

    # Penalise frantic switching (people talking over each other); a calm
    # back-and-forth (~1 switch every few seconds) is barely touched.
    switch_penalty = min(0.4, max(0.0, switch_rate - 0.2) * 0.6)
    coverage = min(1.0, total / dur)  # how much of the window is actually speech

    score = (base - switch_penalty) * (0.6 + 0.4 * coverage)
    return max(0.0, min(1.0, score))


def rank_clips_by_speaker(clips: List[Any], diar_segments: List[Dict[str, Any]],
                          weight: float = 0.15) -> List[Any]:
    """Nudge each clip's ``.score`` by its speaker coherence and return the clips
    sorted best-first. Mutates ``clip.score`` in place (a bounded multiplier in
    ``[1-weight, 1+weight]``) so it reorders ties without steamrolling the base
    highlight ranking. Any clip whose bounds can't be scored is left unchanged.

    ``clips`` need only expose ``start_time``, ``end_time`` and ``score`` — works
    with ClipCandidate as well as lightweight test doubles.
    """
    for c in clips or []:
        try:
            coh = speaker_coherence(c.start_time, c.end_time, diar_segments)
            c.score = float(c.score) * (1.0 + weight * (2.0 * coh - 1.0))
        except Exception:  # noqa: BLE001
            continue
    return sorted(clips or [], key=lambda c: getattr(c, "score", 0.0), reverse=True)


def diarize(audio_or_video_path: str, hf_token: Optional[str] = None) -> Dict[str, Any]:
    """Return {"available": bool, "segments": [{start,end,speaker}], "message": str}.

    Never raises for the common "not installed / no token" cases — the caller
    can surface the message and offer the install instead.
    """
    if not diarization_available():
        return {
            "available": False,
            "segments": [],
            "message": "Speaker diarization needs the optional 'pyannote.audio' package. "
                       "Install it and set a Hugging Face token to enable it.",
        }
    token = hf_token or os.environ.get("HUGGINGFACE_TOKEN") or os.environ.get("HF_TOKEN")
    if not token:
        return {
            "available": False,
            "segments": [],
            "message": "pyannote is installed, but a Hugging Face token is required "
                       "(set HF_TOKEN) to download the diarization model.",
        }
    try:
        from pyannote.audio import Pipeline
        pipeline = Pipeline.from_pretrained(
            "pyannote/speaker-diarization-3.1", use_auth_token=token
        )
        annotation = pipeline(audio_or_video_path)
        segments: List[Dict[str, Any]] = []
        for turn, _, speaker in annotation.itertracks(yield_label=True):
            segments.append({
                "start": round(float(turn.start), 3),
                "end": round(float(turn.end), 3),
                "speaker": str(speaker),
            })
        speakers = sorted({s["speaker"] for s in segments})
        return {
            "available": True,
            "segments": segments,
            "speakers": speakers,
            "message": f"Detected {len(speakers)} speaker(s) across {len(segments)} turns.",
        }
    except Exception as e:  # noqa: BLE001
        return {"available": False, "segments": [], "message": f"Diarization failed: {e}"}
