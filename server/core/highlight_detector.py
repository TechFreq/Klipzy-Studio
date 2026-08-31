"""
Highlight & viral moment detection.
Combines heuristic virality scoring, audio energy analysis, and optional LLM-based discovery.
"""

import re
from typing import List, Tuple
from server.models import ClipCandidate, TranscriptSegment, ViralityBreakdown, WordTimestamp

# Words that make a strong opening line / hook.
HOOK_WORDS = [
    "secret", "never", "always", "why", "how to", "how i", "how we", "mistake",
    "truth", "crazy", "insane", "stop", "unbelievable", "wait", "actually",
    "nobody", "everyone", "important", "listen", "the thing is", "here's",
    "you won't believe", "the reason", "what happened", "the best", "the worst",
]
# Emotional / high-energy words that tend to travel well on short-form.
EMOTION_WORDS = [
    "love", "hate", "amazing", "incredible", "shocking", "scary", "hilarious",
    "wild", "epic", "worst", "best", "favorite", "obsessed", "changed my life",
]
# Low-value openers/fillers to strip from the front of a title.
LEADING_FILLER = [
    "um", "uh", "er", "ah", "so", "and", "but", "like", "well", "okay", "ok",
    "you know", "i mean", "basically", "yeah", "right",
]


def _split_sentences(text: str) -> List[str]:
    """Split text into sentences, keeping the terminal punctuation."""
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        return []
    parts = re.split(r"(?<=[.!?])\s+", text)
    return [p.strip() for p in parts if p.strip()]


def _score_sentence(s: str) -> float:
    """Rank a single sentence for hook potential (higher = better opener)."""
    lower = s.lower()
    score = 0.0
    for h in HOOK_WORDS:
        if h in lower:
            score += 2.0
    if "?" in s:               # questions are strong openers
        score += 2.5
    if re.search(r"\d", s):    # concrete numbers
        score += 1.0
    for e in EMOTION_WORDS:
        if e in lower:
            score += 1.0
    wc = len(s.split())
    if 5 <= wc <= 28:          # substantial but not rambling
        score += 1.5
    elif wc < 4:               # penalize fragments like "Hi." / "Right."
        score -= 4.0
    if s.endswith("!"):
        score += 0.5
    return score


def _clean_title(sentence: str, max_chars: int = 60) -> str:
    """Turn a raw spoken sentence into a tidy title."""
    s = re.sub(r"\s+", " ", (sentence or "").strip())
    # Strip a leading filler word ("So, ...", "Um ...", "And ...").
    words = s.split()
    while words:
        first = re.sub(r"[^A-Za-z']", "", words[0]).lower()
        if first in LEADING_FILLER:
            words.pop(0)
        else:
            break
    s = " ".join(words).strip(" ,.-")
    if not s:
        return ""
    # Trim to a word boundary near the limit; keep a trailing '?' if present.
    keep_question = s.endswith("?")
    if len(s) > max_chars:
        cut = s[:max_chars].rsplit(" ", 1)[0].rstrip(" ,.-")
        s = cut + "…"
    elif not keep_question:
        s = s.rstrip(".")
    return s[0].upper() + s[1:] if s else s


def choose_hook_and_title(full_text: str, fallback_index: int = 1) -> Tuple[str, str]:
    """
    Pick the strongest complete sentence in a clip window as the hook, and a
    tidy title from it. Beats using the first raw segment (often a fragment like
    "Hi."). Works with no LLM at all.
    """
    sentences = _split_sentences(full_text)
    if not sentences:
        return (f"Highlight {fallback_index}", f"Highlight {fallback_index}")
    # Prefer the best-scoring sentence; on a tie, an earlier sentence wins so the
    # hook still reflects how the clip actually opens.
    best = max(range(len(sentences)), key=lambda i: (_score_sentence(sentences[i]), -i))
    hook_sentence = sentences[best]
    # If the very best is still a weak fragment, fall back to the longest sentence.
    if _score_sentence(hook_sentence) <= 0 and len(sentences) > 1:
        hook_sentence = max(sentences, key=lambda s: len(s.split()))
    title = _clean_title(hook_sentence) or _clean_title(sentences[0]) or f"Highlight {fallback_index}"
    hook_text = hook_sentence if len(hook_sentence) <= 140 else hook_sentence[:137].rsplit(" ", 1)[0] + "…"
    return (hook_text, title)


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

                            # Pick the strongest complete sentence as the hook/title
                            # instead of the first raw segment (often a fragment).
                            hook_text, title = choose_hook_and_title(full_txt, len(candidates) + 1)

                            candidates.append(
                                ClipCandidate(
                                    id=f"clip_{len(candidates) + 1}",
                                    title=title,
                                    start_time=current_start,
                                    end_time=seg.end,
                                    duration=round(current_duration, 2),
                                    score=score,
                                    hook_text=hook_text,
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