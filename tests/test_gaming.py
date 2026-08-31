"""Gaming/reaction layout auto-detection — pure-helper tests (no video needed)."""

from server.core.face_tracker import FaceTracker


def test_corner_of():
    assert FaceTracker._corner_of(10, 10, 100, 100) == "top-left"
    assert FaceTracker._corner_of(90, 10, 100, 100) == "top-right"
    assert FaceTracker._corner_of(90, 90, 100, 100) == "bottom-right"
    assert FaceTracker._corner_of(10, 90, 100, 100) == "bottom-left"


def test_facecam_candidate_detects_corner_inset():
    w, h = 1920, 1080
    # A small person box tucked in the bottom-right corner = webcam inset.
    inset = (1560, 760, 1880, 1050)  # ~17% width, corner
    cand = FaceTracker._facecam_candidate([inset], w, h)
    assert cand is not None
    corner, scale, area = cand
    assert corner == "bottom-right"
    assert 0.1 < scale < 0.25


def test_facecam_candidate_ignores_centered_talking_head():
    w, h = 1920, 1080
    # A large centered person = normal interview, NOT gameplay+cam.
    centered = (700, 100, 1200, 1050)
    assert FaceTracker._facecam_candidate([centered], w, h) is None


def test_facecam_candidate_ignores_large_corner_person():
    w, h = 1920, 1080
    # Big person that happens to be off-center is not a tiny webcam inset.
    big = (1000, 100, 1900, 1050)
    assert FaceTracker._facecam_candidate([big], w, h) is None


def test_detect_gaming_layout_missing_file_is_safe():
    out = FaceTracker().detect_gaming_layout("does_not_exist.mp4")
    assert out["is_gaming"] is False
    assert out["confidence"] == 0.0
