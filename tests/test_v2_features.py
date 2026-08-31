"""
Tests for v2 features:
- Active speaker-aware face tracking & trajectory
- Overlay manager & emoji suggestions
- Multi-aspect export endpoint
"""

import os
from pathlib import Path
from fastapi.testclient import TestClient

from server.models import TranscriptSegment, WordTimestamp
from server.core.face_tracker import FaceTracker
from server.core.overlay_manager import (
    suggest_emojis_for_segments,
    inject_emojis_into_transcript,
)
import server.api.server as srv


def test_face_tracker_aspect_widths():
    """Verify target crop widths for standard video heights across aspect ratios."""
    tracker = FaceTracker()
    # 1080p source (1920x1080)
    w_9_16 = tracker._calc_target_crop_width(1920, 1080, "9:16")
    assert w_9_16 == int(1080 * 9 / 16)  # 607px

    w_1_1 = tracker._calc_target_crop_width(1920, 1080, "1:1")
    assert w_1_1 == 1080

    w_4_5 = tracker._calc_target_crop_width(1920, 1080, "4:5")
    assert w_4_5 == int(1080 * 4 / 5)  # 864px


def test_face_tracker_fallback_when_file_missing():
    """Missing or invalid video path should return None gracefully."""
    tracker = FaceTracker()
    crop_x = tracker.get_speaker_center_x("non_existent_video.mp4", 0.0, 5.0)
    assert crop_x is None

    traj = tracker.get_speaker_trajectory("non_existent_video.mp4", 0.0, 5.0)
    assert traj == []


def test_overlay_emoji_suggestions():
    """Transcript segments containing hook keywords should receive matching emoji suggestions."""
    segments = [
        TranscriptSegment(
            id=1,
            start=0.0,
            end=4.0,
            text="This crazy secret made me a lot of money in tech",
            words=[
                WordTimestamp(word="This", start=0.0, end=0.4),
                WordTimestamp(word="crazy", start=0.5, end=0.9),
                WordTimestamp(word="secret", start=1.0, end=1.5),
                WordTimestamp(word="money", start=2.0, end=2.5),
                WordTimestamp(word="in", start=2.6, end=2.8),
                WordTimestamp(word="tech", start=2.9, end=3.4),
            ],
        )
    ]
    suggestions = suggest_emojis_for_segments(segments)
    assert len(suggestions) >= 3
    words_found = {s["word"].lower() for s in suggestions}
    assert "crazy" in words_found
    assert "secret" in words_found
    assert "money" in words_found


def test_overlay_transcript_emoji_injection():
    """inject_emojis_into_transcript adds emojis to segment text."""
    segments = [
        TranscriptSegment(
            id=1,
            start=0.0,
            end=3.0,
            text="Stop this is a crazy idea",
            words=[
                WordTimestamp(word="Stop", start=0.0, end=0.5),
                WordTimestamp(word="this", start=0.6, end=0.8),
                WordTimestamp(word="is", start=0.9, end=1.0),
                WordTimestamp(word="a", start=1.1, end=1.2),
                WordTimestamp(word="crazy", start=1.3, end=1.8),
                WordTimestamp(word="idea", start=1.9, end=2.4),
            ],
        )
    ]
    injected = inject_emojis_into_transcript(segments)
    assert "🛑" in injected[0].text
    assert "🤯" in injected[0].text
    assert "💡" in injected[0].text


def test_api_suggest_emojis_endpoint():
    """POST /tools/suggest-emojis endpoint returns structured suggestions."""
    client = TestClient(srv.app)
    payload = {
        "segments": [
            {
                "id": 1,
                "start": 0.0,
                "end": 2.0,
                "text": "Winner gets cash and fire",
                "words": [
                    {"word": "Winner", "start": 0.0, "end": 0.5, "probability": 1.0},
                    {"word": "cash", "start": 0.6, "end": 1.0, "probability": 1.0},
                    {"word": "fire", "start": 1.1, "end": 1.5, "probability": 1.0},
                ],
            }
        ]
    }
    res = client.post("/tools/suggest-emojis", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert "suggestions" in data
    assert len(data["suggestions"]) >= 2


# ---------------------------------------------------------------------------
# Transcription backend reporter (MLX / faster-whisper / openai-whisper)
# ---------------------------------------------------------------------------
def test_detect_active_backend_shape():
    """The reporter returns a well-formed dict without loading any model."""
    from server.core.transcriber import detect_active_backend

    info = detect_active_backend()
    assert set(info) == {"active", "available", "apple_silicon", "note"}
    assert info["active"] in {"mlx", "faster-whisper", "openai-whisper", None}
    assert isinstance(info["available"], list)
    assert isinstance(info["apple_silicon"], bool)
    # mlx is only ever selected on Apple Silicon
    if info["active"] == "mlx":
        assert info["apple_silicon"] is True


def test_health_reports_transcription_backend():
    """/health surfaces the active backend so the UI can display it."""
    client = TestClient(srv.app)
    r = client.get("/health")
    assert r.status_code == 200
    assert "transcription_backend" in r.json()


def test_transcription_backend_endpoint():
    client = TestClient(srv.app)
    r = client.get("/transcription-backend")
    assert r.status_code == 200
    body = r.json()
    assert "active" in body and "available" in body
