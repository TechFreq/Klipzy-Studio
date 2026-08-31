"""
LLM-based highlight discovery via Ollama (optional).
Sends transcript to a local LLM to identify viral moments. Falls back gracefully.
"""

import json
from typing import List

from server.models import ClipCandidate, TranscriptSegment


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

        prompt = f"""You are a viral short-form video editor. Given this transcript with timestamps,
identify the 3-5 most engaging, clip-worthy moments (20-60 seconds each).
Return ONLY a JSON array of objects with keys: start, end, title, reason.
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
            candidates.append(
                ClipCandidate(
                    id=f"llm_{i}",
                    title=item.get("title", f"AI Highlight #{i}"),
                    start_time=float(item["start"]),
                    end_time=float(item["end"]),
                    duration=round(float(item["end"]) - float(item["start"]), 2),
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