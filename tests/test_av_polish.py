"""
Audio/visual polish helpers — pure filtergraph-builder tests (no ffmpeg run).
Covers loudness normalization, background music + ducking, and the auto-zoom
push-in expression.
"""

from server.core.ffmpeg_tools import build_audio_filter_chain, build_zoompan_filter


def test_audio_chain_noop_when_nothing_requested():
    chains, out = build_audio_filter_chain(normalize=False, music_label=None)
    assert chains == [] and out is None


def test_audio_chain_normalize_only():
    chains, out = build_audio_filter_chain(normalize=True)
    assert out == "[aout]"
    joined = ";".join(chains)
    assert "loudnorm=I=-14.0" in joined
    # Terminal label is [aout] so render_clip can map it.
    assert chains[-1].endswith("[aout]")


def test_audio_chain_custom_loudness_target():
    chains, _ = build_audio_filter_chain(normalize=True, loudness_target=-16.0)
    assert "loudnorm=I=-16.0" in ";".join(chains)


def test_audio_chain_music_with_ducking():
    chains, out = build_audio_filter_chain(
        normalize=True, music_label="2:a", music_volume=0.1, duck=True)
    joined = ";".join(chains)
    assert out == "[aout]"
    # Speech is normalized, split, and drives the sidechain compressor.
    assert "loudnorm" in joined
    assert "asplit=2[spmain][spsc]" in joined
    assert "sidechaincompress" in joined
    assert "volume=0.100" in joined
    assert "amix=inputs=2:duration=first" in joined


def test_audio_chain_music_without_ducking_has_no_sidechain():
    chains, out = build_audio_filter_chain(
        normalize=False, music_label="1:a", duck=False)
    joined = ";".join(chains)
    assert out == "[aout]"
    assert "sidechaincompress" not in joined
    assert "asplit" not in joined  # no split needed when not ducking
    assert "amix=inputs=2" in joined


def test_zoompan_filter_uses_explicit_size_and_fps():
    f = build_zoompan_filter("[v]", "[v]", 608, 1080, 30.0, zoom_max=1.08)
    assert f.startswith("[v]zoompan=")
    assert "s=608x1080" in f
    assert "fps=30.000" in f
    assert "1.080" in f  # zoom_max ceiling present
    assert f.endswith("[v]")


def test_zoompan_filter_clamps_fps_floor():
    # fps of 0 would be invalid; the builder floors it to >= 1.
    f = build_zoompan_filter("[0:v]", "[v]", 1920, 1080, 0.0)
    assert "fps=1.000" in f
