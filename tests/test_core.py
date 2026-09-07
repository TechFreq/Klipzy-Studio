"""
Tests for the highlight detector and caption generation.
Run with: python -m pytest tests/ -v
"""

import sys
import os
import re
from pathlib import Path
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
        {"title": "Clip 1", "start_time": 0.0, "end_time": 25.0, "hook_text": "Secret tips"}
    ]
    capcut_out = tmp_path / "capcut.json"
    export_capcut_draft("dummy_video.mp4", clips, str(capcut_out))
    assert capcut_out.exists()
    content = capcut_out.read_text(encoding="utf-8")
    assert "CapCut" in content
    assert "9:16" in content


def test_karaoke_caption_generation(tmp_path):
    from server.core.caption_styler import generate_karaoke_captions
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
    generate_karaoke_captions([seg], str(ass_out))
    assert ass_out.exists()
    content = ass_out.read_text(encoding="utf-8")
    assert "[Script Info]" in content
    # Active-word highlight: each word event colors the current word (\c...) and
    # resets the rest to the style default (\r).
    assert "\\c" in content and "\\r" in content


def test_karaoke_captions_custom_font_size(tmp_path):
    from server.core.caption_styler import generate_karaoke_captions
    from server.models import TranscriptSegment, WordTimestamp

    seg = TranscriptSegment(
        id=1,
        start=0.0,
        end=1.0,
        text="Big text",
        words=[
            WordTimestamp(word="Big", start=0.0, end=0.5),
            WordTimestamp(word="text", start=0.5, end=1.0),
        ],
    )
    ass_out = tmp_path / "font.ass"
    generate_karaoke_captions([seg], str(ass_out), style_preset="viral_yellow", font_size=64)
    content = ass_out.read_text(encoding="utf-8")
    # The Style line should embed the requested font size.
    assert re.search(r"Style: Default,.*?,64,", content)
    # Leaving font_size unset falls back to the preset default.
    default_out = tmp_path / "default.ass"
    generate_karaoke_captions([seg], str(default_out), style_preset="viral_yellow")
    default_content = default_out.read_text(encoding="utf-8")
    assert not re.search(r"Style: Default,.*?,64,", default_content)


def test_caption_presets_library(tmp_path):
    from server.core.caption_presets import CAPTION_PRESETS, get_available_presets
    from server.core.caption_styler import generate_karaoke_captions

    presets = get_available_presets()
    assert len(presets) >= 20, f"Expected 20+ presets, found {len(presets)}"
    
    seg = TranscriptSegment(
        id=1,
        start=0.0,
        end=3.0,
        text="Viral caption preset test",
        words=[
            WordTimestamp(word="Viral", start=0.0, end=1.0),
            WordTimestamp(word="caption", start=1.0, end=2.0),
            WordTimestamp(word="test", start=2.0, end=3.0),
        ]
    )
    for p in ["viral_yellow", "neon_green", "cyberpunk_cyan", "mrbeast_impact", "monochrome_chic", "gaming_rgb"]:
        out = tmp_path / f"test_{p}.ass"
        generate_karaoke_captions([seg], str(out), style_preset=p)
        assert out.exists()
        content = out.read_text(encoding="utf-8")
        # Per-word active highlight: the current word is recolored inline.
        assert "\\c" in content
        assert "Default" in content


def test_silence_speech_segment_calculation():
    from server.core.silence_cutter import calculate_speech_segments
    silences = [
        {"start": 3.0, "end": 6.0, "duration": 3.0},
        {"start": 12.0, "end": 15.0, "duration": 3.0},
    ]
    speech = calculate_speech_segments(total_duration=20.0, silence_intervals=silences, pad_seconds=0.1)
    assert len(speech) == 3
    assert speech[0][0] == 0.0
    assert speech[0][1] > 2.5
    assert speech[2][1] == 20.0


def test_profanity_filter_detection():
    from server.core.word_filter import sanitize_text, find_profanity_timestamps
    text = "This is a fucking crazy and holy shit podcast"
    sanitized = sanitize_text(text)
    assert "f*****g" in sanitized or "f**k" in sanitized
    assert "s**t" in sanitized

    seg = TranscriptSegment(
        id=1,
        start=0.0,
        end=4.0,
        text="holy shit that is wild",
        words=[
            WordTimestamp(word="holy", start=0.0, end=0.8),
            WordTimestamp(word="shit", start=0.8, end=1.5),
            WordTimestamp(word="that", start=1.5, end=2.0),
        ]
    )
    swear_ts = find_profanity_timestamps([seg])
    assert len(swear_ts) == 1
    assert swear_ts[0]["word"] == "shit"
    assert swear_ts[0]["start"] == 0.8


def test_bleep_filter_word_timestamps_precision():
    """The quick-bleep path sends every clip word; the server must keep ONLY
    profanity, and must not over-match innocent words that merely contain a
    swear substring (peacock/Dickens/class)."""
    from server.core.word_filter import filter_word_timestamps
    words = [
        {"word": "This", "start": 0.0, "end": 0.3},
        {"word": "peacock", "start": 0.3, "end": 0.8},   # contains 'cock' -> NOT profane
        {"word": "Dickens", "start": 0.8, "end": 1.2},   # contains 'dick' -> NOT profane
        {"word": "class", "start": 1.2, "end": 1.6},     # contains 'ass' -> NOT profane
        {"word": "fucking", "start": 1.6, "end": 2.0},   # profane
        {"word": "bullshit", "start": 2.0, "end": 2.4},  # profane via 'shit' stem
    ]
    hits = filter_word_timestamps(words)
    assert {h["word"] for h in hits} == {"fucking", "bullshit"}
    assert all("start" in h and "end" in h and "duration" in h for h in hits)


def test_bleep_custom_words():
    from server.core.word_filter import filter_word_timestamps
    words = [{"word": "banana", "start": 0.0, "end": 0.5}]
    assert filter_word_timestamps(words) == []
    hits = filter_word_timestamps(words, custom_words=["banana"])
    assert len(hits) == 1 and hits[0]["word"] == "banana"


def test_filler_elongation_matching():
    from server.core.filler_cutter import _collapse_elongation, DEFAULT_FILLERS
    assert _collapse_elongation("uhhh") == "uh"
    assert _collapse_elongation("ummm") == "um"
    assert _collapse_elongation("errr") == "er"
    collapsed = {_collapse_elongation(f) for f in DEFAULT_FILLERS}
    # Elongated variants normalize to a known filler.
    assert _collapse_elongation("uhhhh") in collapsed
    assert _collapse_elongation("ahhh") in collapsed


# ---------------------------------------------------------------------------
# ffmpeg_tools refactor tests
# ---------------------------------------------------------------------------
def test_build_filter_chain_full_passthrough_is_empty():
    from server.core.ffmpeg_tools import build_filter_chain
    # Full-frame no-crop no-PiP -> no filtergraph (stream-copy fast path).
    assert build_filter_chain(layout="full", aspect_ratio=None, has_cam=False) == []
    assert build_filter_chain(layout="", aspect_ratio=None, has_cam=False) == []


def test_build_filter_chain_vertical_crop():
    from server.core.ffmpeg_tools import build_filter_chain
    chain = build_filter_chain(layout="", aspect_ratio="9:16", has_cam=False)
    assert len(chain) == 1 and chain[0].startswith("[0:v]crop=")
    # Both dimensions must be bounded by the source. `crop=ih*9/16:ih` asked for
    # a window wider than vertical footage and ffmpeg failed the encode.
    assert "min(iw" in chain[0] and "min(ih" in chain[0]


def test_build_filter_chain_crop_ratios_are_source_bounded():
    """Regression: every crop ratio must clamp to the source in BOTH axes, or
    exporting that ratio from already-vertical footage dies in the encoder."""
    from server.core.ffmpeg_tools import CROP_RATIOS, build_filter_chain

    for ratio in CROP_RATIOS:
        chain = build_filter_chain(layout="", aspect_ratio=ratio, has_cam=False)
        assert len(chain) == 1, ratio
        assert "min(iw" in chain[0] and "min(ih" in chain[0], ratio


def test_build_filter_chain_16x9_letterboxes_instead_of_cropping():
    """16:9 must FIT the whole frame and pad the remainder. Cropping to 16:9
    turned a vertical clip into a strip (and previously output 340x606 — not
    even 16:9), so landscape delivery pads instead."""
    from server.core.ffmpeg_tools import build_filter_chain
    chain = build_filter_chain(layout="full", aspect_ratio="16:9", has_cam=False)
    assert len(chain) == 1
    assert "pad=w=max(iw" in chain[0]
    assert "setsar=1" in chain[0]
    assert "crop=" not in chain[0]


def test_crop_expr_clamps_speaker_offset_in_frame():
    """An active-speaker x offset must never push the window off the frame."""
    from server.core.ffmpeg_tools import build_crop_expr

    expr = build_crop_expr(9, 16, "1500")
    assert "max(1500" in expr and "iw-ow" in expr
    # No offset -> centred.
    assert "(iw-ow)/2" in build_crop_expr(9, 16, None)


def test_build_filter_chain_game_reaction_needs_cam():
    from server.core.ffmpeg_tools import build_filter_chain
    # Without a cam, game_reaction degrades to a plain passthrough.
    chain = build_filter_chain(layout="game_reaction", has_cam=False)
    assert chain == []
    chain2 = build_filter_chain(layout="game_reaction", has_cam=True)
    assert len(chain2) == 1
    assert "overlay=" in chain2[0]


def test_detect_hw_encoder_is_cached():
    from server.core import ffmpeg_tools
    # Reset cache
    ffmpeg_tools._HW_ENCODER_CACHE = None
    first = ffmpeg_tools.detect_hw_encoder()
    second = ffmpeg_tools.detect_hw_encoder()
    assert first == second
    assert ffmpeg_tools._HW_ENCODER_CACHE is not None


def test_x264_fallback_args_constant():
    from server.core.ffmpeg_tools import X264_FALLBACK_ARGS, detect_hw_encoder
    # When no HW encoder, we should return the same fallback args used elsewhere.
    assert X264_FALLBACK_ARGS == ["-preset", "fast", "-crf", "22"]


# ---------------------------------------------------------------------------
# API validation tests (happy-path + guards) using FastAPI TestClient
# ---------------------------------------------------------------------------
def _make_client():
    from fastapi.testclient import TestClient
    import server.api.server as srv
    return TestClient(srv.app)


def test_process_rejects_bad_max_clips():
    client = _make_client()
    here = os.path.abspath(os.path.join(os.path.dirname(__file__), "test_core.py"))
    r = client.post("/process", json={
        "video_path": here,
        "max_clips": 0,
    })
    assert r.status_code == 400
    assert "max_clips" in r.json()["detail"]


def test_process_rejects_bad_duration_range():
    client = _make_client()
    here = os.path.abspath(os.path.join(os.path.dirname(__file__), "test_core.py"))
    r = client.post("/process", json={
        "video_path": here,
        "max_clips": 5,
        "min_duration": 60.0,
        "max_duration": 20.0,
    })
    assert r.status_code == 400
    assert "max_duration" in r.json()["detail"]


def test_trim_rejects_zero_length():
    client = _make_client()
    here = os.path.abspath(os.path.join(os.path.dirname(__file__), "test_core.py"))
    r = client.post("/trim", json={
        "video_path": here,
        "start_seconds": 5.0,
        "end_seconds": 5.0,
    })
    assert r.status_code == 400


def test_render_custom_rejects_bad_layout_and_ratio():
    client = _make_client()
    here = os.path.abspath(os.path.join(os.path.dirname(__file__), "test_core.py"))
    r = client.post("/render/custom", json={
        "video_path": here,
        "start_seconds": 0,
        "end_seconds": 10,
        "layout": "spinning_cube",
    })
    assert r.status_code == 400
    assert "layout" in r.json()["detail"]


def test_subtitle_time_shifting(tmp_path):
    from server.core.ffmpeg_tools import _shift_subtitle_times

    ass_path = tmp_path / "sample.ass"
    ass_path.write_text(
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:01:05.50,0:01:08.25,Default,,0,0,0,,{\\k50}HELLO {\\k50}WORLD\n",
        encoding="utf-8"
    )

    rebased_ass = _shift_subtitle_times(str(ass_path), 60.0)
    assert os.path.exists(rebased_ass)
    content = Path(rebased_ass).read_text(encoding="utf-8")
    assert "Dialogue: 0,0:00:05.50,0:00:08.25,Default" in content

    srt_path = tmp_path / "sample.srt"
    srt_path.write_text(
        "1\n"
        "00:01:10,500 --> 00:01:14,200\n"
        "Hello World\n",
        encoding="utf-8"
    )

    rebased_srt = _shift_subtitle_times(str(srt_path), 60.0)
    assert os.path.exists(rebased_srt)
    srt_content = Path(rebased_srt).read_text(encoding="utf-8")
    assert "00:00:10,500 --> 00:00:14,200" in srt_content


def test_project_delete_removes_whole_generated_folder(tmp_path):
    """Deleting a project must remove the entire output/<job_id> folder (clips,
    captions, transcripts, temp audio/re-render files) without touching the
    original source video."""
    client = _make_client()

    # The source video lives outside the engine output directory.
    source_video = tmp_path / "podcast.mp4"
    source_video.write_bytes(b"\x00\x00\x00\x18ftypmp42junk")

    # Simulate a generated clip: output/<job_id>/clip_1.mp4 plus temp files.
    import server.api.server as srv
    job_folder = Path(srv.ENGINE.output_dir) / "8fd7a3f2"
    job_folder.mkdir(parents=True, exist_ok=True)
    (job_folder / "clip_1.mp4").write_bytes(b"clip-data")
    (job_folder / "clip_1.srt").write_text("subtitle", encoding="utf-8")
    (job_folder / "clip_1.ass").write_text("ass", encoding="utf-8")
    (job_folder / "captions.srt").write_text("captions", encoding="utf-8")
    (job_folder / "captions.vtt").write_text("captions", encoding="utf-8")
    (job_folder / "temp_audio.wav").write_bytes(b"wav")

    r = client.post("/project/delete", json={
        "paths": [
            str(job_folder / "clip_1.mp4"),
            str(job_folder / "clip_1.srt"),
            str(job_folder / "clip_1.ass"),
            str(job_folder),
        ]
    })
    assert r.status_code == 200
    assert not job_folder.exists(), "Whole generated job folder should be removed"
    assert source_video.exists(), "Source video must never be deleted"


def test_project_delete_refuses_paths_outside_output_dir(tmp_path):
    """The delete endpoint must refuse to remove anything outside the engine
    output directory (e.g. the original source video)."""
    client = _make_client()
    outside = tmp_path / "precious.mp4"
    outside.write_bytes(b"do-not-delete")
    r = client.post("/project/delete", json={"paths": [str(outside)]})
    assert r.status_code == 200
    assert r.json()["deleted"] == []
    assert outside.exists()


def test_output_folder_switch_updates_runtime(tmp_path):
    """Setting the output folder re-routes new renders/jobs to the chosen
    folder, and deleting still refuses anything outside it."""
    client = _make_client()

    # Default is the repo-anchored output dir.
    r = client.get("/output-folder")
    assert r.status_code == 200
    default_folder = Path(r.json()["folder"])
    assert default_folder.is_dir()

    # Switch to a temp folder; it should be created and reported.
    new_folder = tmp_path / "my productions"
    r = client.post("/output-folder", json={"folder": str(new_folder)})
    assert r.status_code == 200
    assert Path(r.json()["folder"]) == new_folder.resolve()
    assert new_folder.is_dir()

    # A clip generated afterward must live under the new folder.
    import server.api.server as srv
    job_folder = Path(srv.ENGINE.output_dir) / "deadbeef"
    job_folder.mkdir(parents=True, exist_ok=True)
    (job_folder / "clip_1.mp4").write_bytes(b"data")
    assert job_folder.is_relative_to(new_folder.resolve())

    # Deleting that new-folder clip works.
    r = client.post("/project/delete", json={"paths": [str(job_folder)]})
    assert r.status_code == 200
    assert not job_folder.exists()


def test_capcut_style_caption_customization(tmp_path):
    """Test CapCut-style knobs: custom font, hex colors, outline width, bold, italic, uppercase, position."""
    from server.core.caption_styler import generate_karaoke_captions
    from server.models import TranscriptSegment, WordTimestamp

    seg = TranscriptSegment(
        id=1,
        start=0.0,
        end=2.0,
        text="custom style test",
        words=[
            WordTimestamp(word="custom", start=0.0, end=0.7),
            WordTimestamp(word="style", start=0.7, end=1.4),
            WordTimestamp(word="test", start=1.4, end=2.0),
        ]
    )

    ass_out = tmp_path / "custom_styled.ass"
    generate_karaoke_captions(
        [seg],
        str(ass_out),
        style_preset="viral_yellow",
        font_name="Montserrat",
        font_size=42,
        primary_color="#00ffcc",
        highlight_color="#ff007f",
        outline_color="#1a1a1a",
        outline_width=5,
        bold=True,
        italic=True,
        uppercase=True,
        position=5,  # Middle-center
    )

    assert ass_out.exists()
    content = ass_out.read_text(encoding="utf-8")

    # Montserrat font embedded
    assert "Montserrat" in content
    # Font size 42
    assert ",42," in content
    # Bold (-1) and Italic (-1)
    assert ",-1,-1," in content
    # Alignment 5 (middle-center)
    assert ",5," in content
    # Outline width 5
    assert ",5,0,0,0," in content or ",5," in content
    # Text transformed to uppercase in karaoke dialogue lines
    assert "CUSTOM" in content
    assert "STYLE" in content
    assert "TEST" in content
    # Outline color converted to ASS hex (&H001A1A1A)
    assert "&H001A1A1A" in content
    # Highlight color converted to ASS hex in karaoke tag or style
    assert "&H007F00FF" in content


def test_export_subtitles_endpoint_with_custom_styling(tmp_path):
    """Test that /export/subtitles accepts all new CapCut knobs and burns them."""
    client = _make_client()
    ass_out = tmp_path / "endpoint_styled.srt"

    payload = {
        "output_path": str(ass_out),
        "words": [
            {"word": "Hello", "start": 0.0, "end": 0.5},
            {"word": "World", "start": 0.5, "end": 1.0}
        ],
        "style_preset": "neon_green",
        "font_name": "Impact",
        "font_size": 36,
        "primary_color": "#ffffff",
        "highlight_color": "#ffaa00",
        "outline_color": "#000000",
        "outline_width": 4,
        "uppercase": True,
        "bold": True,
        "italic": False,
        "position": 8,  # Top-center
        "re_render": False
    }

    r = client.post("/export/subtitles", json=payload)
    assert r.status_code == 200

    generated_ass = tmp_path / "endpoint_styled.ass"
    assert generated_ass.exists()
    content = generated_ass.read_text(encoding="utf-8")
    assert "Impact" in content
    assert ",36," in content
    assert ",-1,0," in content  # bold=True, italic=False
    assert ",8," in content  # top-center alignment
    assert "HELLO" in content
    assert "WORLD" in content


def test_export_subtitles_endpoint(tmp_path):
    client = _make_client()
    srt_out = str(tmp_path / "edited.srt")
    r = client.post("/export/subtitles", json={
        "output_path": srt_out,
        "words": [
            {"word": "Test", "start": 0.0, "end": 0.5},
            {"word": "Caption", "start": 0.5, "end": 1.2}
        ],
        "style_preset": "neon_green",
        "font_size": 32,
    })
    assert r.status_code == 200
    assert os.path.exists(srt_out)
    ass_out = str(tmp_path / "edited.ass")
    assert os.path.exists(ass_out)
    ass_content = Path(ass_out).read_text(encoding="utf-8")
    assert "Impact" in ass_content
    assert ",32," in ass_content


# ----------------------------------------------------------------------
# Job queue + cancellation + transcript cache tests
# ----------------------------------------------------------------------
def test_jobs_list_and_cancel_unknown():
    from fastapi.testclient import TestClient
    import server.api.server as srv
    client = TestClient(srv.app)

    r = client.post("/job/doesnotexist/cancel")
    assert r.status_code == 404

    r = client.get("/jobs")
    assert r.status_code == 200
    assert isinstance(r.json()["jobs"], list)


def test_cancel_active_job_and_queue():
    """A valid /process that fails fast still enqueues then becomes terminal,
    and /jobs lists it."""
    from fastapi.testclient import TestClient
    import server.api.server as srv
    import time
    client = TestClient(srv.app)

    here = os.path.abspath(os.path.join(os.path.dirname(__file__), "test_core.py"))
    r = client.post("/process", json={
        "video_path": here,
        "max_clips": 1,
        "min_duration": 1.0,
        "max_duration": 30.0,
    })
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    assert job_id in srv._JOB_REQUESTS

    # The fake "video" fails processing -> job goes terminal.
    deadline = time.time() + 10
    while time.time() < deadline:
        job = client.get(f"/job/{job_id}").json()
        if job["status"] in ("completed", "failed", "cancelled"):
            break
        time.sleep(0.05)

    r = client.post(f"/job/{job_id}/cancel")
    assert r.status_code == 200
    assert "Already finished" in r.json()["message"]

    jobs = client.get("/jobs").json()["jobs"]
    assert any(j["job_id"] == job_id for j in jobs)


def test_transcript_cache_roundtrip(tmp_path):
    """Cache saves/loads segments keyed by video fingerprint + model."""
    from server.core.pipeline import VideoClipperEngine
    engine = VideoClipperEngine()
    seg = make_segments()

    video = tmp_path / "fake.mp4"
    video.write_bytes(b"0000")

    model = "base"
    key = f"{engine._video_fingerprint(str(video))}_{model}"
    cache_f = engine._transcript_cache_path() / f"{key}.json"
    if cache_f.exists():
        cache_f.unlink()

    assert engine._cached_segments(str(video), model) is None
    engine._save_cache(str(video), model, seg)
    loaded = engine._cached_segments(str(video), model)
    assert loaded is not None
    assert len(loaded) == len(seg)
    assert loaded[0].text == seg[0].text


def test_export_formats_and_thumbnail(tmp_path):
    """Test webm/av1 format validation and thumbnail extraction support."""
    from fastapi.testclient import TestClient
    import server.api.server as srv
    client = TestClient(srv.app)

    # 400 for unknown format
    fake_clip = tmp_path / "fake.mp4"
    fake_clip.write_bytes(b"0000")

    r = client.post("/export/media", json={
        "video_path": str(fake_clip),
        "format": "invalid_xyz"
    })
    assert r.status_code == 400

    # Model accepts av1 and webm
    from server.models import ExportMediaRequest, ExportCompileRequest
    req_av1 = ExportMediaRequest(video_path=str(fake_clip), format="av1")
    assert req_av1.format == "av1"
    req_webm = ExportMediaRequest(video_path=str(fake_clip), format="webm")
    assert req_webm.format == "webm"

    # Thumbnail endpoint rejects missing file
    r_thumb = client.post("/export/thumbnail", json={
        "video_path": str(tmp_path / "non_existent.mp4"),
        "timestamp": 0.5
    })
    assert r_thumb.status_code == 400


def test_social_metadata_generation():
    """Verify social metadata, catchy titles, hashtags, and disclaimer generation."""
    from fastapi.testclient import TestClient
    import server.api.server as srv
    client = TestClient(srv.app)

    r = client.post("/social/metadata", json={
        "title": "Secret to AI coding",
        "hook_text": "This mistake will cost you everything in tech",
        "full_text": "We discuss how technology and AI are changing coding workflows.",
        "duration": 35.5
    })
    assert r.status_code == 200
    data = r.json()
    assert "🔥" in data["title"]
    assert len(data["hashtags"]) >= 6
    assert "#shorts" in data["hashtags"]
    assert "#technology" in data["hashtags"] or "#tech" in data["hashtags"]
    assert "Disclaimer:" in data["formatted_post"] or len(data["disclaimer"]) > 0


# ---------------------------------------------------------------------------
# Multi-aspect crop geometry (preview + export share this math)
# ---------------------------------------------------------------------------

def _ratio_of(rect):
    w, h, _x, _y = rect
    return w / h


def test_compute_crop_rect_landscape_source():
    """A wide source is height-limited: full height, narrowed width, centred."""
    from server.core.ffmpeg_tools import compute_crop_rect

    w, h, x, y = compute_crop_rect(1920, 1080, "9:16")
    assert (h, y) == (1080, 0)
    assert abs(w / h - 9 / 16) < 0.01
    assert x == (1920 - w) // 2          # centred by default
    assert w < 1920                       # actually cropped, not passed through

    assert abs(_ratio_of(compute_crop_rect(1920, 1080, "4:5")) - 4 / 5) < 0.01
    assert abs(_ratio_of(compute_crop_rect(1920, 1080, "1:1")) - 1.0) < 0.01


def test_compute_crop_rect_vertical_source_keeps_target_ratio():
    """Regression: a 4:5 crop of ALREADY-VERTICAL footage must stay 4:5.

    The target width (1920*4/5) exceeds the 1080px source width, so the crop is
    width-limited and the HEIGHT has to shrink. Naively clamping only the width
    left the rect at the source's own 9:16 shape.
    """
    from server.core.ffmpeg_tools import compute_crop_rect

    w, h, x, y = compute_crop_rect(1080, 1920, "4:5")
    assert (w, h) == (1080, 1350)
    assert abs(w / h - 4 / 5) < 0.01
    assert x == 0 and y == (1920 - 1350) // 2   # centred vertically


def test_compute_crop_rect_noop_and_offsets():
    from server.core.ffmpeg_tools import compute_crop_rect

    # Source already matches the target -> full frame (caller skips the filter).
    assert compute_crop_rect(1080, 1920, "9:16") == (1080, 1920, 0, 0)
    assert compute_crop_rect(1080, 1080, "1:1") == (1080, 1080, 0, 0)
    # 16:9 is NOT a crop ratio (it letterboxes), so it has no crop rectangle.
    assert compute_crop_rect(1920, 1080, "16:9") is None

    # Active-speaker offset is honoured, but clamped to stay inside the frame.
    w, _h, x, _y = compute_crop_rect(1920, 1080, "9:16", crop_x_offset=400)
    assert x == 400
    _w2, _h2, x2, _y2 = compute_crop_rect(1920, 1080, "9:16", crop_x_offset=99999)
    assert x2 == 1920 - w                 # pinned to the right edge, never off-frame
    _w3, _h3, x3, _y3 = compute_crop_rect(1920, 1080, "9:16", crop_x_offset=-500)
    assert x3 == 0                        # and never negative


def test_compute_crop_rect_even_dimensions_and_guards():
    from server.core.ffmpeg_tools import compute_crop_rect

    # Odd source dimensions still yield even crop sizes (encoder-safe).
    w, h, _x, _y = compute_crop_rect(1921, 1081, "9:16")
    assert w % 2 == 0 and h % 2 == 0

    # Unknown ratio / unusable dimensions degrade to None (caller: no crop).
    assert compute_crop_rect(1920, 1080, "3:7") is None
    assert compute_crop_rect(0, 1080, "9:16") is None
    assert compute_crop_rect(1920, 0, "9:16") is None


# ---------------------------------------------------------------------------
# Virality breakdown honesty: never report a score nothing measured
# ---------------------------------------------------------------------------

def test_virality_breakdown_has_no_flattering_defaults():
    """Regression: these defaulted to 8.5/8.0/9.0/'High', so any detector that
    computed no breakdown still rendered confident numbers. Unknown must be
    None so the card can show '-' instead of inventing analysis."""
    from server.models import ViralityBreakdown

    empty = ViralityBreakdown()
    assert empty.hook_score is None
    assert empty.flow_score is None
    assert empty.engagement_score is None
    assert empty.trend_potential is None


def test_audio_energy_clips_report_only_measured_signals():
    """The energy detector measures loudness + duration, not hook wording."""
    from server.core.audio_energy import _flow_from_duration, _trend_from_score

    # Flow peaks near the ~35s sweet spot and degrades away from it.
    assert _flow_from_duration(35.0) == 10.0
    assert _flow_from_duration(5.0) < _flow_from_duration(30.0)
    # Bounded to the documented 6-10 range whatever the length.
    for d in (0.5, 12.0, 35.0, 90.0, 600.0):
        assert 6.0 <= _flow_from_duration(d) <= 10.0

    assert _trend_from_score(9.0) == "Very High"
    assert _trend_from_score(7.5) == "High"
    assert _trend_from_score(5.0) == "Good"


def test_heuristic_detector_still_fills_a_full_breakdown():
    """The keyword detector DOES analyse hook wording, so it must keep
    reporting hook_score (this is the one detector that legitimately can)."""
    from server.core.highlight_detector import HighlightDetector
    from server.models import TranscriptSegment, WordTimestamp

    words = [WordTimestamp(word=w, start=float(i), end=float(i) + 0.5)
             for i, w in enumerate(
                 ("here is the secret nobody tells you about why this "
                  "always works and the truth is insane").split())]
    seg = TranscriptSegment(id=0, start=0.0, end=25.0,
                            text=" ".join(w.word for w in words), words=words)

    det = HighlightDetector(min_duration=5.0, max_duration=60.0)
    clips = det.detect_highlights_heuristic([seg])
    assert clips, "expected the keyword detector to find a candidate"
    v = clips[0].virality
    assert v is not None
    assert v.hook_score is not None      # genuinely measured from keywords
    assert v.trend_potential in ("Good", "High", "Very High")
    assert v.hook_keywords                # the words it actually matched
