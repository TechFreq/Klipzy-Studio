"""
LLM-based highlight discovery via Ollama (optional).
Sends transcript to a local LLM to identify viral moments. Falls back gracefully.
"""

import json
from typing import Any, List, Optional, Tuple

from server.models import ClipCandidate, TranscriptSegment


def _seg_field(seg: Any, key: str, default: float = 0.0) -> float:
    if isinstance(seg, dict):
        return float(seg.get(key, default))
    return float(getattr(seg, key, default))


def _snap_window(
    start: float,
    end: float,
    segments: List[Any],
    min_dur: float = 8.0,
    max_dur: float = 90.0,
) -> Optional[Tuple[float, float]]:
    """Snap an LLM-proposed [start, end] to real transcript sentence boundaries
    and enforce duration bounds. Pure + testable. Returns a clean (start, end)
    or None when the window can't be made valid — the guardrail that stops a
    hallucinated/misaligned timestamp from producing a clip that cuts mid-word.
    """
    if not segments:
        return None
    starts = [_seg_field(s, "start") for s in segments]
    ends = [_seg_field(s, "end") for s in segments]
    lo, hi = min(starts), max(ends)

    start = max(lo, min(float(start), hi))
    end = max(lo, min(float(end), hi))

    ss = min(starts, key=lambda x: abs(x - start))
    ee = min(ends, key=lambda x: abs(x - end))

    if ee <= ss:
        greater = [e for e in ends if e > ss]
        if not greater:
            return None
        ee = min(greater)

    dur = ee - ss
    if dur < min_dur:
        target = ss + min_dur
        greater = [e for e in ends if e >= target]
        ee = min(greater) if greater else max(ends)
        dur = ee - ss
        if dur < min_dur * 0.6:  # still too short to be a real clip
            return None
    if dur > max_dur:
        target = ss + max_dur
        lesser = [e for e in ends if ss < e <= target]
        if lesser:
            ee = max(lesser)
    return (round(ss, 3), round(ee, 3))


def _apply_llm_rankings(
    candidates: List[Any],
    rankings: Any,
    weight: float = 0.6,
) -> List[Any]:
    """Merge LLM judgements onto REAL candidate windows and re-rank. Pure + testable.

    The LLM only references candidate ids (never invents timestamps), so this
    blends its 0-10 quality score into each candidate's score and adopts its
    hook/title/reason when provided. Returns the candidates sorted best-first.
    Ignores malformed entries and out-of-range ids.
    """
    if not candidates or not isinstance(rankings, list):
        return candidates
    by_id = {}
    for r in rankings:
        if not isinstance(r, dict):
            continue
        try:
            idx = int(r.get("id"))
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(candidates):
            by_id[idx] = r

    for i, c in enumerate(candidates):
        r = by_id.get(i)
        if not r:
            continue
        try:
            llm_score = max(0.0, min(10.0, float(r.get("score"))))
            c.score = round((1.0 - weight) * float(c.score) + weight * llm_score, 3)
        except (TypeError, ValueError):
            pass
        hook = str(r.get("hook") or "").strip()
        if hook:
            c.hook_text = hook[:120]
        title = str(r.get("title") or "").strip()
        if title:
            c.title = title[:90]
        reason = str(r.get("reason") or "").strip()
        if reason:
            c.reason = reason[:200]
    return sorted(candidates, key=lambda c: getattr(c, "score", 0.0), reverse=True)


def _coerce_rankings(data: Any) -> list:
    """Normalize an LLM's JSON reply into a list of ranking dicts.

    With Ollama's ``format="json"`` most local models return a top-level OBJECT,
    not the array we ask for. They come back in three shapes we must handle:
      1. a bare array: ``[{...}, {...}]``
      2. an object wrapping the array: ``{"rankings": [...]}`` / ``{"clips": [...]}``
      3. a SINGLE ranking object: ``{"id": 0, "score": 7, ...}`` — smaller models
         (and even 14B under json mode) often collapse to just the first candidate.
    Returning [] for cases we can't read keeps the caller on the heuristic order.
    """
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        # An object that IS one ranking entry -> wrap it so its judgement counts.
        if "id" in data and ("score" in data or "hook" in data or "title" in data):
            return [data]
        # An object that WRAPS the array under some key.
        for v in data.values():
            if isinstance(v, list):
                return v
    return []


def rank_candidates_llm(
    candidates: List[ClipCandidate],
    model: str = "qwen2.5:7b",
    preset: str = "",
) -> List[ClipCandidate]:
    """Grounded "rank-and-refine" selection: instead of asking the LLM to invent
    clip timestamps (which it hallucinates), give it the REAL candidate windows
    and let it score them + write a hook/title. Falls back to the unchanged
    candidates if Ollama isn't available or the response can't be parsed.
    """
    if not candidates:
        return candidates
    try:
        import ollama
    except Exception:
        return candidates

    listing = []
    for i, c in enumerate(candidates):
        txt = (getattr(c, "full_text", "") or getattr(c, "hook_text", "") or "")
        txt = " ".join(str(txt).split())
        dur = getattr(c, "duration", 0.0) or 0.0
        listing.append(f"{i}: [{dur:.0f}s] {txt[:280]}")

    n = len(candidates)
    prompt = (
        "You are a short-form video editor choosing which moments to publish as "
        "standalone vertical shorts. Below are CANDIDATE clips, each as "
        "`id: [duration] transcript`.\n\n"
        f"There are {n} candidates, with ids 0 to {n - 1}. Score EVERY ONE of them "
        "0-10 on how well it works as a standalone short:\n"
        "- is it a complete, self-contained thought (no missing setup)?\n"
        "- does it open with a strong hook and land a clear payoff?\n"
        "- is it a single focused topic?\n\n"
        "For each candidate also write a punchy hook (roughly 4-12 words) and a "
        "short catchy title, grounded in that candidate's own transcript.\n"
        'Return ONLY a JSON object of the form {"rankings": [ ... ]} where "rankings" '
        f"is an array with EXACTLY {n} objects, one for every id 0 to {n - 1}, each "
        '{"id": <int>, "score": <0-10 number>, "hook": "...", "title": "...", "reason": "..."}. '
        "Use only the ids shown; do not invent clips.\n\n"
        "Candidates:\n" + "\n".join(listing)
    )

    try:
        resp = ollama.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            format="json",
        )
        content = (resp.get("message", {}) or {}).get("content", "") or ""
        data = _coerce_rankings(json.loads(content))
        return _apply_llm_rankings(candidates, data)
    except Exception:
        return candidates


def detect_highlights_llm(segments: List[TranscriptSegment], model: str = "gemma2:2b") -> List[ClipCandidate]:
    """
    Sends transcript summary to a local LLM (Ollama) to identify viral moments.
    Returns [] if Ollama is unavailable.
    """
    try:
        import ollama

        compact = "\n".join(
            f"[{seg.start:.1f}-{seg.end:.1f}] {seg.text}" for seg in segments[:200]
        )

        prompt = f"""You are a viral short-form video editor. From this timestamped transcript,
pick the 3-5 most engaging moments to cut as standalone shorts (20-60s each).

Choose by MEANING, not just loud moments:
- Each clip must be a COMPLETE THOUGHT — start where a new idea/topic begins and
  end where it resolves, so it makes sense on its own with no missing setup.
- Prefer a strong hook line, a clear payoff, and a single topic per clip.
- Snap start/end to natural sentence boundaries in the transcript timestamps.
- Do not overlap clips or cut mid-sentence.

Return ONLY a JSON array of objects with keys: start, end, title, reason
(reason = why it works as a self-contained clip).
Transcript:
{compact}"""

        response = ollama.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            format="json",
        )
        content = response["message"]["content"]

        data = json.loads(content)
        candidates = []
        for i, item in enumerate(data):
            try:
                raw_start = float(item["start"])
                raw_end = float(item["end"])
            except (KeyError, TypeError, ValueError):
                continue
            # Ground the model's timestamps: snap to real sentence boundaries and
            # drop windows that can't be made valid, so we never emit a clip that
            # starts/ends mid-sentence from a hallucinated time.
            snapped = _snap_window(raw_start, raw_end, segments)
            if not snapped:
                continue
            start_time, end_time = snapped
            candidates.append(
                ClipCandidate(
                    id=f"llm_{i}",
                    title=item.get("title", f"AI Highlight #{i}"),
                    start_time=start_time,
                    end_time=end_time,
                    duration=round(end_time - start_time, 2),
                    score=9.0,
                    hook_text=item.get("reason", "")[:60],
                    full_text=item.get("reason", ""),
                    reason=item.get("reason", "LLM-selected moment"),
                )
            )
        return _deduplicate(candidates)
    except Exception:
        return []


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