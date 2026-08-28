"""
Highlight & viral moment detection.
Combines heuristic virality scoring, audio energy analysis, and optional LLM-based discovery.
"""

from typing import List
from server.models import ClipCandidate, TranscriptSegment, ViralityBreakdown, WordTimestamp


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
            accumulated_words: List[WordTimestamp] = []

            for j in range(i, n):
                seg = segments[j]
                accumulated_text.append(seg.text)
                for w in (seg.words or []):
                    accumulated_words.append(w)
                current_duration = seg.end - current_start

                if current_duration >= self.min_duration:
                    if current_duration <= self.max_duration:
                        full_txt = " ".join(accumulated_text)
                        score = self._compute_virality_score(full_txt)
                        if score >= 5.0:
                            # Calculate detailed virality breakdown
                            hook_score = round(min(10.0, score * 1.05), 1)
                            flow_score = round(min(10.0, max(6.0, 10.0 - abs(current_duration - 35.0) * 0.15)), 1)
                            eng_score = round(min(10.0, hook_score * 0.6 + flow_score * 0.4), 1)
                            trend = "Very High" if score >= 8.5 else ("High" if score >= 7.0 else "Good")
                            
                            found_hooks = [h for h in ["secret", "never", "always", "why", "how to", "mistake", "truth", "crazy", "insane", "stop", "unbelievable", "wait", "actually"] if h in full_txt.lower()]

                            candidates.append(
                                ClipCandidate(
                                    id=f"clip_{len(candidates) + 1}",
                                    title=f"Highlight #{len(candidates) + 1}",
                                    start_time=current_start,
                                    end_time=seg.end,
                                    duration=round(current_duration, 2),
                                    score=score,
                                    hook_text=accumulated_text[0][:80] + ("..." if len(accumulated_text[0]) > 80 else ""),
                                    full_text=full_txt,
                                    reason=f"Virality Score: {score}/10 | Hook: {found_hooks[0] if found_hooks else 'High Engagement Flow'}",
                                    virality=ViralityBreakdown(
                                        hook_score=hook_score,
                                        flow_score=flow_score,
                                        engagement_score=eng_score,
                                        trend_potential=trend,
                                        hook_keywords=found_hooks
                                    ),
                                    words=accumulated_words
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