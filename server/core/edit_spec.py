"""
Edit Spec — the single source of truth for the built-in clip editor.

One JSON document describes an edited clip: the canvas (delivery ratio + fps),
and a set of tracks (video / captions / audio / overlay / text). The SAME spec
drives the browser preview and the ffmpeg export compile (see compositor.py), so
what you see while editing is what you get on export — the same "one source of
truth" discipline that fixed the Whisper-model bug.

This module is PURE: `normalize_spec` takes whatever the UI sent and returns a
canonical, fully-defaulted, validated dict (or raises ValueError with a clear
message). No file or ffmpeg I/O here, so it is trivially unit-testable. The
compositor consumes ONLY the normalized output.

Phase 0 scope: canvas + video/captions/audio/overlay tracks are normalized and
compiled. `text` items are validated and preserved but not yet rendered by the
compositor (documented, comes in a later phase).
"""
from __future__ import annotations

from typing import Any, Dict, List

# Delivery ratios the editor canvas supports. "full"/"original" means "keep the
# source frame untouched" (no crop, no pad).
CANVAS_RATIOS = ("9:16", "4:5", "1:1", "16:9", "full")

TRACK_KINDS = ("video", "captions", "audio", "overlay", "text")

DEFAULT_FPS = 30
DEFAULT_RATIO = "9:16"


def _num(value: Any, default: float = 0.0) -> float:
    """Coerce to float, tolerating None/str; falls back to `default`."""
    try:
        if value is None or value == "":
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _clamp01(value: Any, default: float = 0.5) -> float:
    """Normalized 0..1 coordinate/scale, clamped."""
    v = _num(value, default)
    return max(0.0, min(1.0, v))


def _norm_canvas(canvas: Any) -> Dict[str, Any]:
    canvas = canvas if isinstance(canvas, dict) else {}
    ratio = str(canvas.get("ratio") or DEFAULT_RATIO).strip()
    if ratio == "original":
        ratio = "full"
    if ratio not in CANVAS_RATIOS:
        raise ValueError(f"canvas.ratio must be one of {CANVAS_RATIOS}, got {ratio!r}")
    fps = _num(canvas.get("fps"), DEFAULT_FPS)
    if fps <= 0:
        raise ValueError(f"canvas.fps must be > 0, got {fps}")
    return {"ratio": ratio, "fps": fps}


def _norm_crop(value):
    if not isinstance(value, dict):
        return None
    import math
    vals = {k: float(value.get(k, d)) for k, d in (("x", 0), ("y", 0), ("w", 1), ("h", 1))}
    if not all(math.isfinite(v) for v in vals.values()):
        raise ValueError("Crop coordinates must be finite")
    vals["w"] = max(0.01, min(1, vals["w"]))
    vals["h"] = max(0.01, min(1, vals["h"]))
    vals["x"] = max(0, min(1 - vals["w"], vals["x"]))
    vals["y"] = max(0, min(1 - vals["h"], vals["y"]))
    return vals


def _norm_facecam(value):
    if not isinstance(value, dict) or not value.get("crop"):
        return None
    return {"crop": _norm_crop(value["crop"]),
            "layout": value.get("layout") if value.get("layout") in ("top", "bottom", "pip") else "pip",
            "size": max(0.15, min(0.45, _num(value.get("size"), 0.3))),
            "corner": value.get("corner") if value.get("corner") in ("top-left", "top-right", "bottom-left", "bottom-right") else "bottom-right"}


def _norm_video_clip(item: Any, idx: int) -> Dict[str, Any]:
    if not isinstance(item, dict) or not item.get("src"):
        raise ValueError(f"video clip #{idx} needs a 'src'")
    start = max(0.0, _num(item.get("start"), 0.0))
    tin = max(0.0, _num(item.get("in"), 0.0))
    tout = _num(item.get("out"), 0.0)
    if tout <= tin:
        raise ValueError(f"video clip #{idx}: 'out' ({tout}) must be > 'in' ({tin})")
    transform = item.get("transform") if isinstance(item.get("transform"), dict) else {}
    zoom = _num(transform.get("zoom"), 1.0) or 1.0
    # Optional colour filter names (e.g. "warm", "bw"); the compositor maps known
    # names to ffmpeg filters and silently ignores unknown ones.
    raw_filters = item.get("filters")
    if isinstance(raw_filters, str):
        raw_filters = [raw_filters]
    filters = [str(f) for f in (raw_filters or []) if f and str(f) != "none"]
    return {
        "src": str(item["src"]),
        "in": tin,
        "out": tout,
        "start": start,
        "transform": {
            # cropRatio defaults to the canvas ratio (filled by caller if absent).
            "cropRatio": transform.get("cropRatio"),
            "x": transform.get("x"),            # active-speaker offset (px) or None
            "zoom": max(1.0, zoom),
            "crop": _norm_crop(transform.get("crop")),
            "facecam": _norm_facecam(transform.get("facecam")),
        },
        "filters": filters,
        "speed": max(0.1, _num(item.get("speed"), 1.0) or 1.0),
        # Fade/transition to/from a colour at the clip's start/end (seconds).
        # 0 = none. Colour is "black" (fade) or "white" (flash).
        "fadeIn": max(0.0, _num(item.get("fadeIn"), 0.0)),
        "fadeOut": max(0.0, _num(item.get("fadeOut"), 0.0)),
        "fadeInColor": "white" if str(item.get("fadeInColor")).lower() == "white" else "black",
        "fadeOutColor": "white" if str(item.get("fadeOutColor")).lower() == "white" else "black",
        # Original-clip audio level (1.0 = unchanged).
        "volume": max(0.0, min(4.0, _num(item.get("volume"), 1.0))),
    }


def _norm_timed_item(item: Any, idx: int, kind: str) -> Dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError(f"{kind} item #{idx} must be an object")
    start = max(0.0, _num(item.get("start"), 0.0))
    end = _num(item.get("end"), 0.0)
    if end and end <= start:
        raise ValueError(f"{kind} item #{idx}: 'end' ({end}) must be > 'start' ({start})")
    return {"start": start, "end": end}


def _norm_overlay(item: Any, idx: int) -> Dict[str, Any]:
    base = _norm_timed_item(item, idx, "overlay")
    if not item.get("src"):
        raise ValueError(f"overlay item #{idx} needs a 'src'")
    pos = item.get("pos") if isinstance(item.get("pos"), dict) else {}
    base.update({
        "src": str(item["src"]),
        "pos": {"x": _clamp01(pos.get("x"), 0.5), "y": _clamp01(pos.get("y"), 0.5)},
        "scale": max(0.01, min(4.0, _num(item.get("scale"), 0.25) or 0.25)),
    })
    return base


def _norm_text(item: Any, idx: int) -> Dict[str, Any]:
    base = _norm_timed_item(item, idx, "text")
    text = str(item.get("text") or "").strip()
    if not text:
        raise ValueError(f"text item #{idx} needs non-empty 'text'")
    pos = item.get("pos") if isinstance(item.get("pos"), dict) else {}
    base.update({
        "text": text,
        "template": item.get("template"),
        "style": item.get("style") if isinstance(item.get("style"), dict) else {},
        "pos": {"x": _clamp01(pos.get("x"), 0.5), "y": _clamp01(pos.get("y"), 0.85)},
        "anim": item.get("anim"),
    })
    return base


def _norm_audio(item: Any, idx: int) -> Dict[str, Any]:
    base = _norm_timed_item(item, idx, "audio")
    if not item.get("src"):
        raise ValueError(f"audio item #{idx} needs a 'src'")
    base.update({
        "src": str(item["src"]),
        "gain": max(0.0, min(4.0, _num(item.get("gain"), 0.12))),
        "duck": bool(item.get("duck", True)),
        # Music beds loop to cover the clip; one-shot SFX do not. Default True
        # keeps the Phase-1 single-music behaviour (a looped bed).
        "loop": bool(item.get("loop", True)),
        # Optional role hint for the UI ("music" | "sfx"); not used by the compile.
        "role": str(item.get("role") or "music"),
    })
    return base


def _norm_track(track: Any, idx: int, canvas_ratio: str) -> Dict[str, Any]:
    if not isinstance(track, dict):
        raise ValueError(f"track #{idx} must be an object")
    kind = str(track.get("kind") or "").strip()
    if kind not in TRACK_KINDS:
        raise ValueError(f"track #{idx}: kind must be one of {TRACK_KINDS}, got {kind!r}")

    if kind == "video":
        clips = [_norm_video_clip(c, i) for i, c in enumerate(track.get("clips") or [])]
        # Default each clip's crop to the canvas ratio when it didn't specify one.
        for c in clips:
            if not c["transform"]["cropRatio"]:
                c["transform"]["cropRatio"] = canvas_ratio
        return {"kind": "video", "clips": clips}

    if kind == "captions":
        src = str(track.get("source") or "asr")
        return {"kind": "captions", "source": src, "template": track.get("template") or "viral_yellow"}

    items_key = "items"
    raw_items = track.get(items_key) or []
    if kind == "overlay":
        items = [_norm_overlay(it, i) for i, it in enumerate(raw_items)]
    elif kind == "text":
        items = [_norm_text(it, i) for i, it in enumerate(raw_items)]
    else:  # audio
        items = [_norm_audio(it, i) for i, it in enumerate(raw_items)]
    return {"kind": kind, "items": items}


def normalize_spec(spec: Any) -> Dict[str, Any]:
    """Validate + canonicalize a raw edit spec. Raises ValueError on invalid input."""
    if not isinstance(spec, dict):
        raise ValueError("edit spec must be an object")
    if not spec.get("source"):
        raise ValueError("edit spec needs a top-level 'source' video path")

    canvas = _norm_canvas(spec.get("canvas"))
    tracks_raw = spec.get("tracks")
    if tracks_raw is not None and not isinstance(tracks_raw, list):
        raise ValueError("'tracks' must be a list")
    tracks = [_norm_track(t, i, canvas["ratio"]) for i, t in enumerate(tracks_raw or [])]

    duration = _num(spec.get("duration"), 0.0)
    if duration < 0:
        raise ValueError("duration must be >= 0")

    return {
        "version": int(spec.get("version") or 1),
        "source": str(spec["source"]),
        "canvas": canvas,
        "duration": duration,
        "tracks": tracks,
        "transitions": spec.get("transitions") if isinstance(spec.get("transitions"), list) else [],
    }


def tracks_of_kind(spec: Dict[str, Any], kind: str) -> List[Dict[str, Any]]:
    """All normalized tracks of a given kind (order preserved)."""
    return [t for t in spec.get("tracks", []) if t.get("kind") == kind]
