"""
Tests for the highlight detector and caption generation.
Run with: python -m pytest tests/ -v
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from server.models import TranscriptSegment, WordTimestamp
from server.core.highlight_detector import HighlightDetector
from server.core.ffmpeg_tools import generate_srt, generate_vtt


def make_segments():
    """Create a fake transcript with a viral moment."""
    texts = [
        "Welcome back to the podcast everyone.",
        "Today we're talking about the secret that nobody talks about.",
        "Why do most creators fail? It's simple, they never stop.",
        "The truth is, everyone makes the same mistake.",
        "And that's why you need to listen to this part carefully.",
        "Thanks for watching, see you next time.",
    ]
    segments = []
    t = 0.0
    for i, text in enumerate(texts):
        seg = TranscriptSegment(
            id=i,
            start=round(t, 2),
            end=round(t + 5.0, 2),
            text=text,
            words=[
                WordTimestamp(word=w, start=t + j * 0.4, end=t + j * 0.4 + 0.4, probability=0.95)
                for j, w in enumerate(text.split())
            ],
        )
        segments.append(seg)
        t += 5.0
    return segments


def test_heuristic_detection_finds_highlights():
    segments = make_segments()
    detector = HighlightDetector(min_duration=5.0, max_duration=15.0)
    clips = detector.detect_highlights_heuristic(segments)

    assert len(clips) > 0, "Should find at least one highlight"
    # The viral segment should be detected
    assert any("secret" in c.full_text.lower() or "mistake" in c.full_text.lower() for c in clips)


def test_detector_scores_hooks():
    detector = HighlightDetector()
    score = detector._compute_virality_score("This is the secret that nobody talks about, why do we never stop?")
    assert score >= 5.0, "Hook keywords should boost score"


def test_deduplicate_overlapping():
    detector = HighlightDetector()
    from server.models import ClipCandidate
    c1 = ClipCandidate(id="a", title="A", start_time=0, end_time=10, duration=10, score=9, hook_text="", full_text="", reason="")
    c2 = ClipCandidate(id="b", title="B", start_time=5, end_time=15, duration=10, score=8, hook_text="", full_text="", reason="")
    c3 = ClipCandidate(id="c", title="C", start_time=20, end_time=30, duration=10, score=7, hook_text="", full_text="", reason="")
    result = detector._deduplicate([c1, c2, c3])
    assert len(result) == 2, "Overlapping clips should be removed"


def test_srt_generation(tmp_path):
    segments = make_segments()
    out = generate_srt(segments, str(tmp_path / "test.srt"))
    assert os.path.exists(out)
    content = open(out, encoding="utf-8").read()
    assert "WEBVTT" not in content
    assert "-->" in content


def test_vtt_generation(tmp_path):
    segments = make_segments()
    out = generate_vtt(segments, str(tmp_path / "test.vtt"))
    assert os.path.exists(out)
    content = open(out, encoding="utf-8").read()
    assert content.startswith("WEBVTT")


def test_fcpxml_export(tmp_path):
    from server.core.export_tools import export_fcpxml
    clips = [
        {"title": "Hook 1", "start_time": 10.0, "end_time": 30.0},
        {"title": "Hook 2", "start_time": 45.0, "end_time": 65.0}
    ]
    xml_out = tmp_path / "test.xml"
    export_fcpxml("dummy_video.mp4", clips, str(xml_out), fps=30.0)
    assert xml_out.exists()
    content = xml_out.read_text(encoding="utf-8")
    assert "<xmeml" in content
    assert "Hook 1" in content


def test_capcut_draft_export(tmp_path):
    from server.core.export_tools import export_capcut_draft
    clips = [
        {"title": "Opus Clip 1", "start_time": 0.0, "end_time": 25.0, "hook_text": "Secret tips"}
    ]
    capcut_out = tmp_path / "capcut.json"
    export_capcut_draft("dummy_video.mp4", clips, str(capcut_out))
    assert capcut_out.exists()
    content = capcut_out.read_text(encoding="utf-8")
    assert "CapCut" in content
    assert "9:16" in content


def test_animated_ass_generation(tmp_path):
    from server.core.caption_styler import generate_animated_ass
    from server.models import TranscriptSegment, WordTimestamp
    
    seg = TranscriptSegment(
        id=1,
        start=0.0,
        end=2.0,
        text="This is amazing",
        words=[
            WordTimestamp(word="This", start=0.0, end=0.5),
            WordTimestamp(word="is", start=0.5, end=1.0),
            WordTimestamp(word="amazing", start=1.0, end=2.0)
        ]
    )
    ass_out = tmp_path / "test.ass"
    generate_animated_ass([seg], str(ass_out))
    assert ass_out.exists()
    content = ass_out.read_text(encoding="utf-8")
    assert "[Script Info]" in content
    assert "\\k" in content