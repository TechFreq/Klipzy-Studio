"""
#16.3 Diarization-driven active-speaker crop — pure-helper tests (no video, no
pyannote). Covers the deterministic pieces: speaker->position aggregation,
active-speaker lookup, trajectory building, and the ffmpeg crop x-expression.
The video-sampling method (get_diarized_speaker_trajectory) needs real
multi-person footage and is exercised manually.
"""

from server.core.face_tracker import FaceTracker
from server.core.ffmpeg_tools import build_crop_x_expression


def test_aggregate_speaker_positions_uses_median():
    samples = [("A", 100.0), ("A", 200.0), ("A", 150.0), ("B", 900.0)]
    pos = FaceTracker._aggregate_speaker_positions(samples)
    assert pos["A"] == 150.0
    assert pos["B"] == 900.0


def test_aggregate_ignores_none_speaker():
    pos = FaceTracker._aggregate_speaker_positions([(None, 10.0), ("A", 20.0)])
    assert pos == {"A": 20.0}


def test_active_speaker_at():
    diar = [{"start": 0.0, "end": 5.0, "speaker": "A"},
            {"start": 5.0, "end": 10.0, "speaker": "B"}]
    assert FaceTracker._active_speaker_at(2.0, diar) == "A"
    assert FaceTracker._active_speaker_at(7.0, diar) == "B"
    assert FaceTracker._active_speaker_at(99.0, diar) is None


def test_build_diarized_trajectory_follows_speakers():
    diar = [{"start": 0.0, "end": 5.0, "speaker": "A"},
            {"start": 5.0, "end": 10.0, "speaker": "B"}]
    speaker_pos = {"A": 200.0, "B": 1400.0}  # A on the left, B on the right
    width, target = 1920, 1080  # max_off = 840
    traj = FaceTracker._build_diarized_trajectory(
        0.0, 10.0, diar, speaker_pos, width, target, sample_fps=4.0)
    assert traj, "expected a trajectory"
    # Clip-local timestamps start at 0.
    assert traj[0]["timestamp"] == 0.0
    # Early on we track speaker A (left, crop_x clamped near 0); by the end we've
    # panned toward B (right, larger crop_x).
    assert traj[0]["crop_x"] < traj[-1]["crop_x"]
    assert 0 <= traj[-1]["crop_x"] <= (width - target)


def test_build_diarized_trajectory_empty_without_positions():
    diar = [{"start": 0.0, "end": 5.0, "speaker": "A"}]
    assert FaceTracker._build_diarized_trajectory(0, 5, diar, {}, 1920, 1080) == []


def test_build_diarized_trajectory_empty_when_no_crop_needed():
    # Frame narrower than the crop width -> nothing to pan.
    diar = [{"start": 0.0, "end": 5.0, "speaker": "A"}]
    assert FaceTracker._build_diarized_trajectory(0, 5, diar, {"A": 100.0}, 500, 1080) == []


def test_crop_expression_none_when_static():
    # A single point, or points that never move, => no dynamic expression.
    assert build_crop_x_expression([], 0, 800) is None
    assert build_crop_x_expression([(0.0, 100.0)], 0, 800) is None
    flat = [{"timestamp": t, "crop_x": 100} for t in (0.0, 1.0, 2.0, 3.0)]
    assert build_crop_x_expression(flat, 0, 800) is None


def test_crop_expression_builds_for_movement():
    kf = [{"timestamp": 0.0, "crop_x": 0},
          {"timestamp": 2.0, "crop_x": 400},
          {"timestamp": 4.0, "crop_x": 800}]
    expr = build_crop_x_expression(kf, 0, 800)
    assert expr is not None
    # Time-driven, clamped ffmpeg expression.
    assert "clip(" in expr
    assert "if(lt(t," in expr
    assert "800.0" in expr and "400.0" in expr


def test_crop_expression_decimates_tiny_moves():
    # A 2px wobble around 100 is below min_delta -> treated as static.
    kf = [{"timestamp": 0.0, "crop_x": 100},
          {"timestamp": 1.0, "crop_x": 101},
          {"timestamp": 2.0, "crop_x": 99},
          {"timestamp": 3.0, "crop_x": 100}]
    assert build_crop_x_expression(kf, 0, 800, min_delta=6.0) is None
