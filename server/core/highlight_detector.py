"""
Highlight & viral moment detection.
Combines heuristic scoring, audio energy analysis, and optional LLM-based discovery.
"""

from typing import List

from server.models import ClipCandidate, TranscriptSegment


class HighlightDetector:
    def __init__(self, min_duration: float = 20.0, max_duration: float = 60.0):
        self.min_duration = min_duration
        self.max_duration = max_duration

    # ------------------------------------------------------------------
    # Heuristic detection
    # ------------------------------------------------------------------
    def detect_highlights_heuristic(self, segments: List[TranscriptSegment]) -> List[ClipCandidate]:
        if not segments:
            return []

        candidates: List[ClipCandidate] = []
        n = len(segments)

        for i in range(n):
            current_start = segments[i].start
            accumulated_text: List[str] = []

            for j in range(i, n):
                seg = segments[j]
                accumulated_text.append(seg.text)
                current_duration = seg.end - current_start

                if current_duration >= self.min_duration:
                    if current_duration <= self.max_duration:
                        full_txt = " ".join(accumulated_text)
                        score = self._compute_virality_score(full_txt)
                        if score >= 5.0:
                            candidates.append(
                                ClipCandidate(
                                    id=f"clip_{len(candidates) + 1}",
                                    title=f"Highlight #{len(candidates) + 1}",
                                    start_time=current_start,
                                    end_time=seg.end,
                                    duration=round(current_duration, 2),
                                    score=score,
                                    hook_text=accumulated_text[0][:60] + "...",
                                    full_text=full_txt,
                                    reason="High conversational engagement / hook keywords",
                                )
                            )
                            break
                    else:
                        break

        candidates.sort(key=lambda c: c.score, reverse=True)
        return self._deduplicate(candidates)

    # ------------------------------------------------------------------
    # Scoring helpers
    # ------------------------------------------------------------------
    def _compute_virality_score(self, text: str) -> float:
        score = 5.0
        lower = text.lower()

        hooks = [
            "secret", "never", "always", "why", "how to", "mistake", "truth",
            "crazy", "insane", "stop", "unbelievable", "wait", "actually",
            "the thing is", "nobody", "everyone", "important", "listen",
        ]
        for h in hooks:
            if h in lower:
                score += 1.5

        if "?" in text:
            score += 1.0

        words = len(text.split())
        if words > 40:
            score += 1.0

        if "!" in text:
            score += 0.5

        return min(round(score, 1), 10.0)

    def _deduplicate(self, clips: List[ClipCandidate], overlap_threshold: float = 0.5) -> List[ClipCandidate]:
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