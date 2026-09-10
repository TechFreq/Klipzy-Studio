"""
Compositor — compiles a normalized Edit Spec (edit_spec.py) into ONE ffmpeg
command and runs it.

The whole editor is built on the idea that the render primitives already exist:
this module reuses the SAME crop/letterbox/audio builders the clip pipeline uses
(server/core/ffmpeg_tools.py), so an edited export can't drift from a generated
clip. It builds a single `filter_complex` (no multi-pass re-encoding) and encodes
with the detected hardware encoder.

Split for testability:
  * `build_export_command(...)` is PURE — given a normalized spec plus an encoder
    and (optional) source dimensions, it returns the exact argv list + the cwd to
    run it in. No probing, no subprocess, so it unit-tests without ffmpeg.
  * `render(...)` is the thin runtime wrapper: it resolves the hardware encoder
    and source dimensions, calls the pure builder, and runs it through
    `proc.run` (so a job cancel hard-kills it and partials are cleaned up).

Phase 0 handles: reframe (crop/letterbox to the canvas ratio) + optional zoom,
image overlays (timed, positioned, scaled), burned captions, and one background
music track (gain + optional sidechain duck). Text items, multi-music, filters
and transitions are normalized by edit_spec but compiled in later phases.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from server.core import proc
from server.core.edit_spec import normalize_spec, tracks_of_kind
from server.core.ffmpeg_tools import (
    CROP_RATIOS,
    LETTERBOX_RATIOS,
    X264_FALLBACK_ARGS,
    build_crop_expr,
    build_letterbox_chain,
    build_audio_filter_chain,
    build_zoompan_filter,
    detect_hw_encoder,
    probe_video_dims,
)


def _primary_video(spec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The first clip of the first video track, if any."""
    for t in tracks_of_kind(spec, "video"):
        if t.get("clips"):
            return t["clips"][0]
    return None


def _first_music(spec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for t in tracks_of_kind(spec, "audio"):
        if t.get("items"):
            return t["items"][0]
    return None


def _overlays(spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for t in tracks_of_kind(spec, "overlay"):
        items.extend(t.get("items") or [])
    return items


def _reframe_chain(ratio: str, x_expr: Optional[str]) -> Optional[str]:
    """The crop/letterbox filter body for a canvas ratio (no in/out labels), or
    None for a full-frame passthrough. Reuses the tested pipeline builders."""
    if ratio in CROP_RATIOS:
        num, den = CROP_RATIOS[ratio]
        return build_crop_expr(num, den, x_expr)
    if ratio in LETTERBOX_RATIOS:
        num, den = LETTERBOX_RATIOS[ratio]
        return build_letterbox_chain(num, den)
    return None  # "full" — untouched frame


def build_export_command(
    spec: Dict[str, Any],
    output_path: str,
    encoder: str,
    enc_args: List[str],
    subtitle_path: Optional[str] = None,
    src_dims: Optional[Tuple[int, int, float]] = None,
    normalize_audio: bool = False,
) -> Tuple[List[str], Optional[str]]:
    """Compile a NORMALIZED spec into an ffmpeg argv + cwd. Pure (no I/O).

    `encoder`/`enc_args` come from detect_hw_encoder(); `src_dims` (w,h,fps) is
    only needed when a zoom is requested (zoompan needs concrete dimensions).
    Pass a normalized spec (call normalize_spec first) — render() does this.
    """
    canvas = spec["canvas"]
    ratio = canvas["ratio"]

    prim = _primary_video(spec)
    source = prim["src"] if prim else spec["source"]
    seek = prim["in"] if prim else 0.0
    if prim:
        duration = prim["out"] - prim["in"]
    else:
        duration = spec.get("duration") or 0.0

    music = _first_music(spec)
    overlays = _overlays(spec)

    # ---- inputs (index order matters for the filtergraph) -----------------
    cmd: List[str] = ["ffmpeg", "-y", "-ss", f"{seek}", "-i", source]
    input_idx = 0
    music_idx = None
    if music:
        input_idx += 1
        music_idx = input_idx
        # Loop the track so a short song still covers the clip; output -t bounds it.
        cmd += ["-stream_loop", "-1", "-i", music["src"]]
    overlay_idx: List[int] = []
    for ov in overlays:
        input_idx += 1
        overlay_idx.append(input_idx)
        cmd += ["-loop", "1", "-i", ov["src"]]
    if duration and duration > 0:
        cmd += ["-t", f"{duration}"]

    # ---- video graph ------------------------------------------------------
    graph: List[str] = []
    x_expr = None
    if prim and prim["transform"].get("x") is not None:
        x_expr = f'{prim["transform"]["x"]}'
    reframe = _reframe_chain(ratio, x_expr)

    cur = "[0:v]"
    if reframe:
        graph.append(f"{cur}{reframe}[vref]")
        cur = "[vref]"

    zoom = prim["transform"]["zoom"] if prim else 1.0
    if zoom and zoom > 1.0 and src_dims:
        sw, sh, sfps = src_dims
        if ratio in CROP_RATIOS:
            num, den = CROP_RATIOS[ratio]
            zw, zh = int(sh * num / den), sh
        elif ratio == "1:1":
            zw = zh = min(sw, sh)
        else:
            zw, zh = sw, sh
        zw -= zw % 2
        zh -= zh % 2
        graph.append(build_zoompan_filter(cur, "[vzoom]", zw, zh, sfps, zoom_max=zoom))
        cur = "[vzoom]"

    # image overlays: scale to a fraction of their own width, then position the
    # centre-ish via the free-space fraction, enabled for the item's time window.
    for n, ov in zip(overlay_idx, overlays):
        graph.append(f"[{n}:v]scale=iw*{ov['scale']:.4f}:-1[ovs{n}]")
        posx = ov["pos"]["x"]
        posy = ov["pos"]["y"]
        end = ov["end"] if ov["end"] else (duration or 0.0)
        enable = f":enable='between(t,{ov['start']:.3f},{end:.3f})'" if end else ""
        graph.append(
            f"{cur}[ovs{n}]overlay=x=(W-w)*{posx:.4f}:y=(H-h)*{posy:.4f}{enable}[ovout{n}]"
        )
        cur = f"[ovout{n}]"

    burn_cwd = None
    if subtitle_path and os.path.exists(subtitle_path):
        # Same Windows-safe technique as render_clip: run in the subtitle dir and
        # reference it by bare filename (a drive-letter colon breaks the filter).
        burn_cwd = os.path.dirname(os.path.abspath(subtitle_path))
        sub_rel = os.path.basename(subtitle_path).replace("'", "'\\''")
        graph.append(f"{cur}subtitles={sub_rel}[vout]")
        cur = "[vout]"

    video_label = cur
    if video_label != "[vout]":
        # Terminate with a single, predictable [vout] label.
        graph.append(f"{video_label}null[vout]")
        video_label = "[vout]"

    # ---- audio graph ------------------------------------------------------
    audio_chains, audio_out = build_audio_filter_chain(
        speech_label="0:a",
        music_label=(f"{music_idx}:a" if music_idx is not None else None),
        normalize=normalize_audio,
        music_volume=(music["gain"] if music else 0.12),
        duck=(music["duck"] if music else True),
    )
    graph += audio_chains

    # ---- assemble ---------------------------------------------------------
    cmd += ["-filter_complex", ";".join(graph)]
    cmd += ["-map", video_label, "-c:v", encoder, *enc_args]
    cmd += ["-map", audio_out if audio_out else "0:a:0?"]
    cmd += ["-c:a", "aac", "-b:a", "192k", output_path]
    return cmd, burn_cwd


def render(
    spec: Dict[str, Any],
    output_path: str,
    subtitle_path: Optional[str] = None,
    normalize_audio: bool = False,
    progress_callback=None,
) -> str:
    """Normalize + compile + run. Returns the output path. Raises on failure;
    a job cancel surfaces as proc.CancelledError (and the partial is removed)."""
    ns = normalize_spec(spec)
    output_path = str(Path(output_path).expanduser().resolve())
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    encoder, enc_args = detect_hw_encoder()

    src_dims = None
    prim = _primary_video(ns)
    if prim and prim["transform"].get("zoom", 1.0) > 1.0:
        src_dims = probe_video_dims(prim["src"])

    cmd, cwd = build_export_command(
        ns, output_path, encoder, enc_args,
        subtitle_path=subtitle_path, src_dims=src_dims, normalize_audio=normalize_audio,
    )

    def _run(command):
        try:
            return proc.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, cwd=cwd)
        except proc.CancelledError:
            try:
                if os.path.exists(output_path):
                    os.remove(output_path)
            except OSError:
                pass
            raise

    if progress_callback:
        progress_callback("Compositing edited clip...", 60)

    result = _run(cmd)
    if result.returncode != 0:
        # Fall back to software x264 if the hardware encoder rejected the graph.
        fb = list(cmd)
        try:
            i = fb.index("-c:v")
            fb[i + 1] = "libx264"
            # Replace the encoder args that followed the encoder name.
            j = i + 2
            k = j
            while k < len(fb) and not fb[k].startswith("-map"):
                k += 1
            fb[j:k] = X264_FALLBACK_ARGS
        except ValueError:
            pass
        res_fb = _run(fb)
        if res_fb.returncode != 0:
            raise RuntimeError(f"Compositor render failed: {res_fb.stderr[-500:]}")

    if progress_callback:
        progress_callback("Done!", 100)
    return output_path
