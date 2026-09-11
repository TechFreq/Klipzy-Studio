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


# Colour-grade presets → ffmpeg filter bodies. Unknown names are ignored so a
# new UI filter can't break a render before the backend knows it.
FILTER_PRESETS: Dict[str, str] = {
    "warm": "eq=gamma_r=1.06:gamma_b=0.94:saturation=1.12",
    "cool": "eq=gamma_b=1.06:gamma_r=0.95:saturation=1.05",
    "vivid": "eq=saturation=1.4:contrast=1.08",
    "bw": "hue=s=0",
    "mono": "hue=s=0",
    "film": "curves=preset=vintage",
}

# Canvas pixel space per ratio, used as the ASS PlayRes for text positioning
# (libass scales it to the real frame, so no probe is needed).
_CANVAS_PX = {
    "9:16": (1080, 1920), "4:5": (1080, 1350), "1:1": (1080, 1080),
    "16:9": (1920, 1080), "full": (1080, 1920),
}


def _all_audio(spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for t in tracks_of_kind(spec, "audio"):
        items.extend(t.get("items") or [])
    return items


def _text_items(spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for t in tracks_of_kind(spec, "text"):
        items.extend(t.get("items") or [])
    return items


def _hex_to_ass(color: Optional[str], default: str = "&H00FFFFFF") -> str:
    """#RRGGBB -> ASS &H00BBGGRR (ASS is BGR with a leading alpha byte)."""
    if not color or not isinstance(color, str):
        return default
    c = color.strip().lstrip("#")
    if len(c) != 6:
        return default
    try:
        r, g, b = c[0:2], c[2:4], c[4:6]
        return f"&H00{b}{g}{r}".upper()
    except Exception:
        return default


def _ass_time(t: float) -> str:
    t = max(0.0, float(t))
    h = int(t) // 3600
    m = (int(t) // 60) % 60
    s = int(t) % 60
    cs = int(round((t - int(t)) * 100))
    if cs >= 100:
        cs = 99
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def text_items_to_ass(spec: Dict[str, Any]) -> Optional[str]:
    """Render free-floating text items to an ASS document (or None if there are
    none). Uses libass (via the subtitles filter) rather than drawtext, which is
    font-config-fragile on Windows — libass is the same engine the captions
    already burn through, so it's proven here. Pure (returns a string)."""
    items = _text_items(spec)
    if not items:
        return None
    ratio = spec.get("canvas", {}).get("ratio", "9:16")
    px_w, px_h = _CANVAS_PX.get(ratio, (1080, 1920))
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {px_w}\nPlayResY: {px_h}\n"
        "WrapStyle: 2\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, "
        "Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Txt,Arial,96,&H00FFFFFF,&H00000000,&H00000000,-1,0,1,4,0,5,10,10,10,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines = [header]
    for it in items:
        text = str(it.get("text") or "").replace("\\", "\\\\").replace("{", "(").replace("}", ")")
        text = text.replace("\r", "").replace("\n", "\\N")
        start = _ass_time(it.get("start") or 0.0)
        end = _ass_time(it.get("end") or ((it.get("start") or 0.0) + 3.0))
        pos = it.get("pos") or {"x": 0.5, "y": 0.85}
        x = int(float(pos.get("x", 0.5)) * px_w)
        y = int(float(pos.get("y", 0.85)) * px_h)
        style = it.get("style") or {}
        size = int(style.get("size") or 96)
        col = _hex_to_ass(style.get("primary"))
        override = f"{{\\an5\\pos({x},{y})\\fs{size}\\c{col}}}"
        lines.append(f"Dialogue: 0,{start},{end},Txt,,0,0,0,,{override}{text}")
    return "\n".join(lines) + "\n"


def _build_multi_audio(items_with_idx, normalize: bool = False, speech_label: str = "0:a"):
    """Mix speech + N music/SFX beds into one track. Each bed gets its own gain
    and optional start delay; if ANY bed asks to duck, the whole bed mix is
    sidechain-compressed under the speech. Returns (chains, out_label).

    Used only for 2+ beds; the 0/1 case stays on the tested
    build_audio_filter_chain so Phase-1 behaviour is unchanged.
    """
    stereo = "aformat=channel_layouts=stereo:sample_rates=48000"
    chains: List[str] = []
    bed_labels: List[str] = []
    any_duck = False
    for k, (idx, item) in enumerate(items_with_idx):
        ops = [f"volume={float(item.get('gain', 0.12)):.3f}", stereo]
        start = float(item.get("start") or 0.0)
        if start > 0:
            ms = int(start * 1000)
            ops.append(f"adelay={ms}|{ms}")
        chains.append(f"[{idx}:a]{','.join(ops)}[m{k}]")
        bed_labels.append(f"[m{k}]")
        if item.get("duck", True):
            any_duck = True

    if len(bed_labels) == 1:
        bed = bed_labels[0]
    else:
        chains.append(f"{''.join(bed_labels)}amix=inputs={len(bed_labels)}:normalize=0[bed]")
        bed = "[bed]"

    sp_ops = []
    if normalize:
        sp_ops.append("loudnorm=I=-14.0:TP=-1.5:LRA=11")
    sp_ops.append(stereo)
    chains.append(f"[{speech_label}]{','.join(sp_ops)}[sp]")
    if any_duck:
        chains.append("[sp]asplit=2[spmain][spsc]")
        chains.append(f"{bed}[spsc]sidechaincompress=threshold=0.05:ratio=8:attack=5:release=250[bedduck]")
        chains.append("[spmain][bedduck]amix=inputs=2:duration=first:normalize=0[aout]")
    else:
        chains.append(f"[sp]{bed}amix=inputs=2:duration=first:normalize=0[aout]")
    return chains, "[aout]"


def _atempo_chain(speed: float) -> List[str]:
    """atempo only accepts 0.5..2.0 per stage, so decompose an arbitrary speed
    into a chain of in-range factors (e.g. 4x -> 2.0,2.0; 0.25x -> 0.5,0.5)."""
    factors: List[str] = []
    s = float(speed)
    if s <= 0:
        return []
    while s > 2.0:
        factors.append("atempo=2.0")
        s /= 2.0
    while s < 0.5:
        factors.append("atempo=0.5")
        s /= 0.5
    if abs(s - 1.0) > 1e-6 or not factors:
        factors.append(f"atempo={s:.4f}")
    return factors


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
    speed = float(prim.get("speed", 1.0)) if prim else 1.0
    if speed <= 0:
        speed = 1.0
    if prim:
        src_span = prim["out"] - prim["in"]
    else:
        src_span = spec.get("duration") or 0.0
    # Speed compresses/stretches the output timeline; the OUTPUT -t must match so
    # a slow-mo isn't cut short and a fast clip isn't padded.
    duration = src_span / speed if src_span else 0.0

    audio_items = _all_audio(spec)
    overlays = _overlays(spec)

    # ---- inputs (index order matters for the filtergraph) -----------------
    cmd: List[str] = ["ffmpeg", "-y", "-ss", f"{seek}", "-i", source]
    input_idx = 0
    audio_input_idx: List[int] = []
    for a in audio_items:
        input_idx += 1
        audio_input_idx.append(input_idx)
        # Music beds loop to cover the clip; one-shot SFX play once.
        if a.get("loop", True):
            cmd += ["-stream_loop", "-1", "-i", a["src"]]
        else:
            cmd += ["-i", a["src"]]
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

    # colour-grade filters (applied to the base video, before overlays so a
    # sticker isn't graded along with the footage).
    filt_names = prim.get("filters") if prim else []
    filt_bodies = [FILTER_PRESETS[f] for f in (filt_names or []) if f in FILTER_PRESETS]
    if filt_bodies:
        graph.append(f"{cur}{','.join(filt_bodies)}[vfilt]")
        cur = "[vfilt]"

    # image overlays: scale to a fraction of their own width, then position the
    # centre-ish via the free-space fraction, enabled for the item's time window.
    for n, ov in zip(overlay_idx, overlays):
        graph.append(f"[{n}:v]scale=iw*{ov['scale']:.4f}:-1[ovs{n}]")
        posx = ov["pos"]["x"]
        posy = ov["pos"]["y"]
        # Overlay/enable times are in ORIGINAL clip time; the final setpts (speed)
        # scales the whole composited stream, so use the pre-speed span here.
        end = ov["end"] if ov["end"] else (src_span or 0.0)
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
        graph.append(f"{cur}subtitles={sub_rel}[vsub]")
        cur = "[vsub]"

    # Post-effects applied to the FULLY composited video, in output order:
    #   speed (setpts) scales the whole stream — including burned overlays/subs —
    #   uniformly; then fade to/from black in the (already sped) output timeline.
    fade_in = float(prim.get("fadeIn", 0.0)) if prim else 0.0
    fade_out = float(prim.get("fadeOut", 0.0)) if prim else 0.0
    fade_in_color = (prim.get("fadeInColor", "black") if prim else "black")
    fade_out_color = (prim.get("fadeOutColor", "black") if prim else "black")
    post: List[str] = []
    if abs(speed - 1.0) > 1e-6:
        post.append(f"setpts=PTS/{speed:.6f}")
    if fade_in > 0:
        post.append(f"fade=t=in:st=0:d={fade_in:.3f}:color={fade_in_color}")
    if fade_out > 0 and duration > 0:
        post.append(f"fade=t=out:st={max(0.0, duration - fade_out):.3f}:d={fade_out:.3f}:color={fade_out_color}")
    if post:
        graph.append(f"{cur}{','.join(post)}[vout]")
        cur = "[vout]"

    video_label = cur
    if video_label != "[vout]":
        # Terminate with a single, predictable [vout] label.
        graph.append(f"{video_label}null[vout]")
        video_label = "[vout]"

    # ---- audio graph ------------------------------------------------------
    # Clip audio level: pre-scale the source audio so "clip volume" affects the
    # original speech only (music/SFX keep their own gains).
    clip_vol = float(prim.get("volume", 1.0)) if prim else 1.0
    speech_label = "0:a"
    if abs(clip_vol - 1.0) > 1e-6:
        graph.append(f"[0:a]volume={clip_vol:.3f}[spv]")
        speech_label = "spv"

    # 0 or 1 bed → reuse the tested single-music builder (Phase-1 behaviour);
    # 2+ beds → the multi-track mixer below (music + layered SFX).
    if len(audio_items) <= 1:
        music = audio_items[0] if audio_items else None
        music_idx = audio_input_idx[0] if audio_items else None
        audio_chains, audio_out = build_audio_filter_chain(
            speech_label=speech_label,
            music_label=(f"{music_idx}:a" if music_idx is not None else None),
            normalize=normalize_audio,
            music_volume=(music["gain"] if music else 0.12),
            duck=(music["duck"] if music else True),
        )
    else:
        audio_chains, audio_out = _build_multi_audio(
            list(zip(audio_input_idx, audio_items)), normalize=normalize_audio,
            speech_label=speech_label,
        )
    graph += audio_chains
    # If only clip-volume was applied (no music mix), map the scaled speech out.
    if audio_out is None and speech_label != "0:a":
        audio_out = "[" + speech_label + "]" if not speech_label.startswith("[") else speech_label

    # Audio post-effects mirror the video: atempo for speed, then afade in/out.
    apost: List[str] = []
    if abs(speed - 1.0) > 1e-6:
        apost += _atempo_chain(speed)
    if fade_in > 0:
        apost.append(f"afade=t=in:st=0:d={fade_in:.3f}")
    if fade_out > 0 and duration > 0:
        apost.append(f"afade=t=out:st={max(0.0, duration - fade_out):.3f}:d={fade_out:.3f}")
    if apost:
        a_in = audio_out if audio_out else "[0:a]"
        graph.append(f"{a_in}{','.join(apost)}[aout]")
        audio_out = "[aout]"

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

    # Free-floating text items become an ASS file burned via the subtitles
    # filter (libass — reliable across platforms, unlike drawtext). Only when
    # the caller didn't already pass a subtitle file to burn.
    if subtitle_path is None:
        ass = text_items_to_ass(ns)
        if ass:
            ass_path = str(Path(output_path).with_suffix(".text.ass"))
            try:
                Path(ass_path).write_text(ass, encoding="utf-8")
                subtitle_path = ass_path
            except OSError:
                subtitle_path = None

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
