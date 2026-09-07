"""
FFmpeg wrapper - cross-platform video/audio manipulation.
Works on Windows and macOS. Requires ffmpeg/ffprobe in PATH.
"""

import json
import os
import platform
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# libx264 fallback args reused across render / silence-cut / bleep / export paths.
X264_FALLBACK_ARGS = ["-preset", "fast", "-crf", "22"]

# Hardware encoder detection is cached: it spawns `ffmpeg -encoders` (a ~200ms
# subprocess) and is currently called on every render, concat, silence cut,
# bleep/mute, and /health poll. One probe per process is plenty.
_HW_ENCODER_CACHE: Optional[Tuple[str, List[str]]] = None


def check_ffmpeg() -> Tuple[bool, Optional[str]]:
    """Check ffmpeg availability. Returns (available, error_message)."""
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg and ffprobe:
        return True, None
    missing = []
    if not ffmpeg:
        missing.append("ffmpeg")
    if not ffprobe:
        missing.append("ffprobe")
    return False, f"Missing: {', '.join(missing)}. Install FFmpeg (winget install Gyan.FFmpeg / brew install ffmpeg)."


def detect_hw_encoder() -> Tuple[str, List[str]]:
    """
    Detects hardware video encoder for maximum rendering speed.
    Supports NVIDIA (NVENC), Apple Silicon/Intel Mac (VideoToolbox), Intel (QSV),
    AMD (AMF), and Linux (VAAPI), falling back to libx264.
    Returns (encoder_name, extra_args). Result is cached for the process lifetime.
    """
    global _HW_ENCODER_CACHE
    if _HW_ENCODER_CACHE is not None:
        return _HW_ENCODER_CACHE

    system = platform.system()
    try:
        res = subprocess.run(["ffmpeg", "-encoders"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        stdout = res.stdout

        # 1. NVIDIA NVENC (Windows / Linux)
        if "h264_nvenc" in stdout:
            _HW_ENCODER_CACHE = ("h264_nvenc", ["-preset", "p4", "-cq", "23"])
            return _HW_ENCODER_CACHE
        # 2. Apple VideoToolbox (macOS Apple Silicon M1-M4 & Intel Mac)
        elif system == "Darwin" and "h264_videotoolbox" in stdout:
            _HW_ENCODER_CACHE = ("h264_videotoolbox", ["-q:v", "65"])
            return _HW_ENCODER_CACHE
        # 3. Intel Quick Sync Video (QSV) (Windows / Linux)
        elif "h264_qsv" in stdout:
            _HW_ENCODER_CACHE = ("h264_qsv", ["-preset", "medium", "-global_quality", "23"])
            return _HW_ENCODER_CACHE
        # 4. AMD Advanced Media Framework (AMF) (Windows / Linux)
        elif "h264_amf" in stdout:
            _HW_ENCODER_CACHE = ("h264_amf", ["-quality", "speed", "-rc", "cqp", "-qp_i", "22", "-qp_p", "22"])
            return _HW_ENCODER_CACHE
        # 5. Linux VAAPI
        elif "h264_vaapi" in stdout:
            _HW_ENCODER_CACHE = ("h264_vaapi", [])
            return _HW_ENCODER_CACHE
    except Exception:
        pass

    _HW_ENCODER_CACHE = ("libx264", list(X264_FALLBACK_ARGS))
    return _HW_ENCODER_CACHE


def get_media_info(file_path: str) -> dict:
    """Inspect video file metadata and streams."""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Input file not found: {file_path}")

    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format", "-show_streams",
        file_path
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr}")
    return json.loads(result.stdout)


def get_video_duration(file_path: str) -> float:
    """Get video duration in seconds."""
    info = get_media_info(file_path)
    return float(info.get("format", {}).get("duration", 0.0))


def extract_audio(video_path: str, output_audio_path: str, sample_rate: int = 16000) -> str:
    """Extract mono 16kHz WAV for Whisper."""
    Path(output_audio_path).parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vn", "-acodec", "pcm_s16le",
        "-ar", str(sample_rate), "-ac", "1",
        output_audio_path
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Audio extraction failed: {result.stderr[-500:]}")
    return output_audio_path


def export_standalone_audio(video_path: str, output_path: str, fmt: str = "mp3") -> str:
    """
    Extract high quality standalone audio (mp3, wav, aac, m4a, flac) from video.
    """
    fmt = fmt.lower().lstrip(".")
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    if not Path(output_path).suffix:
        output_path = f"{output_path}.{fmt}"

    cmd = ["ffmpeg", "-y", "-i", video_path, "-vn"]
    if fmt == "mp3":
        cmd += ["-c:a", "libmp3lame", "-b:a", "320k"]
    elif fmt == "wav":
        cmd += ["-c:a", "pcm_s16le", "-ar", "44100"]
    elif fmt == "aac":
        cmd += ["-c:a", "aac", "-b:a", "256k"]
    elif fmt == "m4a":
        cmd += ["-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart"]
    elif fmt == "flac":
        cmd += ["-c:a", "flac"]
    else:
        cmd += ["-c:a", "libmp3lame", "-b:a", "320k"]
    cmd.append(output_path)

    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Audio export failed: {result.stderr[-500:]}")
    return output_path


def build_crop_x_expression(keyframes, min_x: float, max_x: float, min_delta: float = 6.0) -> Optional[str]:
    """Turn a clip-local crop trajectory into an ffmpeg ``crop`` x-expression that
    linearly follows the active speaker over time (the ffmpeg variable ``t`` is
    the output PTS in seconds, which starts at 0 because render_clip input-seeks
    with ``-ss`` before ``-i``). Pure + testable.

    keyframes: [{"timestamp": t, "crop_x": x}] or [(t, x)] in clip-local seconds.
    Returns None when the motion is negligible (fewer than 2 meaningful points),
    so the caller can fall back to a fixed crop offset. The returned expression
    is meant to be single-quoted in the filtergraph so its commas are literal.
    """
    pts = []
    for k in keyframes or []:
        if isinstance(k, dict):
            pts.append((float(k["timestamp"]), float(k["crop_x"])))
        else:
            pts.append((float(k[0]), float(k[1])))
    if len(pts) < 2:
        return None
    pts.sort(key=lambda p: p[0])

    # Decimate: only keep points that move meaningfully from the last KEPT one
    # (comparing against the last kept point, not the previous sample, so a slow
    # cumulative drift is still preserved while jitter/holds are dropped). A
    # mostly-static speaker collapses to a single point -> no dynamic expression.
    kept = [pts[0]]
    for t, x in pts[1:]:
        if abs(x - kept[-1][1]) >= min_delta:
            kept.append((t, x))
    if len(kept) < 2:
        return None  # essentially static -> caller uses a fixed offset

    expr = f"{kept[-1][1]:.1f}"
    for i in range(len(kept) - 2, -1, -1):
        t0, x0 = kept[i]
        t1, x1 = kept[i + 1]
        dt = (t1 - t0) or 1e-6
        slope = (x1 - x0) / dt
        seg = f"({x0:.1f}+({slope:.4f})*(t-{t0:.3f}))"
        expr = f"if(lt(t,{t1:.3f}),{seg},{expr})"
    expr = f"if(lt(t,{kept[0][0]:.3f}),{kept[0][1]:.1f},{expr})"
    return f"clip({expr},{float(min_x):.1f},{float(max_x):.1f})"


def build_audio_filter_chain(
    speech_label: str = "0:a",
    music_label: Optional[str] = None,
    normalize: bool = False,
    music_volume: float = 0.12,
    duck: bool = True,
    loudness_target: float = -14.0,
) -> Tuple[List[str], Optional[str]]:
    """Build the audio side of the filtergraph for a clip render. Pure + testable.

    Returns (chains, out_label). ``out_label`` is None when there's nothing to do
    (caller then maps the source audio directly). Two effects, composable:

      * normalize -> EBU R128 loudness to ``loudness_target`` LUFS (default -14,
        the level TikTok/YouTube/Reels expect) so clips aren't quiet/inconsistent.
      * music_label -> mix a background track under the speech; when ``duck`` is
        on the music is sidechain-compressed by the speech so it dips whenever
        someone talks (voice stays intelligible).
    """
    if not normalize and not music_label:
        return [], None

    # Force a consistent stereo/48k layout on both branches. sidechaincompress
    # and amix misbehave (or collapse to mono) when the speech and music have
    # different channel counts, so normalise them up front.
    stereo = "aformat=channel_layouts=stereo:sample_rates=48000"

    chains: List[str] = []
    sp_ops = []
    if normalize:
        sp_ops.append(f"loudnorm=I={loudness_target:.1f}:TP=-1.5:LRA=11")
    sp_ops.append(stereo)
    speech_filters = ",".join(sp_ops)

    if not music_label:
        # Normalize-only.
        chains.append(f"[{speech_label}]{speech_filters}[aout]")
        return chains, "[aout]"

    chains.append(f"[{speech_label}]{speech_filters}[sp]")
    if duck:
        # Split speech: one branch is the final voice, the other drives the
        # compressor's sidechain so the music ducks under it.
        chains.append("[sp]asplit=2[spmain][spsc]")
        chains.append(f"[{music_label}]volume={music_volume:.3f},{stereo}[mv]")
        chains.append(
            "[mv][spsc]sidechaincompress="
            "threshold=0.05:ratio=8:attack=5:release=250[mduck]"
        )
        chains.append("[spmain][mduck]amix=inputs=2:duration=first:normalize=0[aout]")
    else:
        chains.append(f"[{music_label}]volume={music_volume:.3f},{stereo}[mv]")
        chains.append("[sp][mv]amix=inputs=2:duration=first:normalize=0[aout]")
    return chains, "[aout]"


def build_zoompan_filter(
    in_label: str,
    out_label: str,
    width: int,
    height: int,
    fps: float,
    zoom_max: float = 1.08,
    zoom_rate: float = 0.0008,
) -> str:
    """Build a gentle continuous "push-in" (Ken Burns) zoompan filter chain entry.
    Pure + testable.

    zoompan needs an explicit output size and fps (its default is 1280x720),
    which is why the caller probes the real frame dimensions first. The zoom
    ramps from 1.0 toward ``zoom_max`` at ``zoom_rate`` per frame and holds,
    centred, so clips get a subtle "professionally edited" motion.
    """
    w, h = int(width), int(height)
    fps = max(1.0, float(fps))
    z = f"min(zoom+{zoom_rate:.4f}\\,{zoom_max:.3f})"
    x = "iw/2-(iw/zoom/2)"
    y = "ih/2-(ih/zoom/2)"
    return (
        f"{in_label}zoompan=z='{z}':x='{x}':y='{y}':d=1:"
        f"s={w}x{h}:fps={fps:.3f}{out_label}"
    )


def probe_video_dims(file_path: str) -> Optional[Tuple[int, int, float]]:
    """Return (width, height, fps) of the first video stream, or None on failure.
    Used by the auto-zoom path, which needs concrete dimensions/fps."""
    try:
        info = get_media_info(file_path)
        for s in info.get("streams", []):
            if s.get("codec_type") == "video":
                w = int(s.get("width", 0))
                h = int(s.get("height", 0))
                rate = s.get("r_frame_rate", "30/1")
                try:
                    num, den = rate.split("/")
                    fps = float(num) / float(den) if float(den) else 30.0
                except Exception:
                    fps = 30.0
                if w > 0 and h > 0:
                    return w, h, (fps or 30.0)
        return None
    except Exception:
        return None


def build_filter_chain(
    aspect_ratio: Optional[str] = None,
    crop_x_offset: Optional[float] = None,
    layout: str = "",
    has_cam: bool = False,
    cam_video: Optional[str] = None,
    cam_scale: float = 0.3,
    cam_position: str = "bottom-right",
    crop_x_expr: Optional[str] = None,
) -> List[str]:
    """
    Build the ffmpeg filtergraph part (without subtitle burn) for a clip render.

    Returns a list of filtergraph sub-graphs; each entry is a complete
    "inputs...label" chain. When an aspect ratio / PiP layout is requested the
    chain emits a `[v]` label that later steps consume. Returns [] for a plain
    full-frame passthrough (no visual processing needed).
    """
    filters: List[str] = []

    if layout == "game_reaction":
        if has_cam:
            base_w, base_h = 1920, 1080
            pad = 6
            cam_w = int(base_w * max(0.1, min(cam_scale, 0.5)))
            cam_h = int(cam_w * 9 / 16)
            box_w, box_h = cam_w + pad * 2, cam_h + pad * 2
            if cam_position == "bottom-left":
                x, y = pad, base_h - box_h - pad
            elif cam_position == "top-right":
                x, y = base_w - box_w - pad, pad
            elif cam_position == "top-left":
                x, y = pad, pad
            else:
                x, y = base_w - box_w - pad, base_h - box_h - pad

            # Camera PiP: scale + letterbox in a 16:9 box, white border, overlay
            # in the chosen corner over the full-frame gameplay.
            base_chain = (
                f"[1:v]scale={cam_w}:{cam_h}:force_original_aspect_ratio=decrease,"
                f"pad={cam_w}:{cam_h}:(ow-iw)/2:(oh-ih)/2:color=0x000000,"
                f"pad={box_w}:{box_h}:{pad}:{pad}:color=0xFFFFFF,setsar=1[cf];"
                f"[0:v][cf]overlay={x}:{y}:eof_action=repeat[v]"
            )
            filters.append(base_chain)
    elif aspect_ratio == "9:16":
        if crop_x_expr:
            # Dynamic active-speaker crop: x follows a time expression (single-
            # quoted so its commas aren't read as filtergraph separators).
            filters.append(f"[0:v]crop=ih*9/16:ih:'{crop_x_expr}':0[v]")
        elif crop_x_offset is not None:
            filters.append(f"[0:v]crop=ih*9/16:ih:{crop_x_offset}:0[v]")
        else:
            filters.append("[0:v]crop=ih*9/16:ih:(iw-ow)/2:0[v]")
    elif aspect_ratio == "1:1":
        if crop_x_expr:
            filters.append(f"[0:v]crop=min(iw\\,ih):min(iw\\,ih):'{crop_x_expr}':(ih-oh)/2[v]")
        elif crop_x_offset is not None:
            filters.append(f"[0:v]crop=min(iw\\,ih):min(iw\\,ih):{crop_x_offset}:(ih-oh)/2[v]")
        else:
            filters.append("[0:v]crop=min(iw\\,ih):min(iw\\,ih):(iw-ow)/2:(ih-oh)/2[v]")
    elif aspect_ratio == "4:5":
        if crop_x_expr:
            filters.append(f"[0:v]crop=ih*4/5:ih:'{crop_x_expr}':0[v]")
        elif crop_x_offset is not None:
            filters.append(f"[0:v]crop=ih*4/5:ih:{crop_x_offset}:0[v]")
        else:
            filters.append("[0:v]crop=ih*4/5:ih:(iw-ow)/2:0[v]")
    elif aspect_ratio == "16:9":
        # Landscape for YouTube - fit the largest 16:9 window inside the frame.
        if crop_x_offset is not None:
            filters.append(
                f"[0:v]scale=w=min(iw\\,ih*16/9):h=min(ih\\,iw*9/16):"
                f"force_original_aspect_ratio=decrease,crop=trunc(iw/2)*2:trunc(ih/2)*2,"
                f"setsar=1,pad=w=trunc(iw/2)*2:h=trunc(ih/2)*2:x={crop_x_offset}:y=0,"
                f"scale=trunc(iw/2)*2:trunc(ih/2)*2[v]"
            )
        else:
            filters.append(
                "[0:v]scale=w=min(iw\\,ih*16/9):h=min(ih\\,iw*9/16):"
                "force_original_aspect_ratio=decrease,crop=trunc(iw/2)*2:trunc(ih/2)*2,"
                "setsar=1[v]"
            )

    return filters


def _shift_subtitle_times(subtitle_path: str, clip_offset: float) -> str:
    """
    ffmpeg's `subtitles` filter also shifts in absolute video PTS. render_clip
    seeks with `-ss` placed BEFORE `-i` (input seek), which resets the output
    timeline so the clip starts at t=0. Caption files generated by the pipeline
    carry ABSOLUTE source timestamps (e.g. a clip cut at 00:05:10 has Dialogue
    events at 05:10.00-05:14.00), so without rebasing those events the burn lands
    outside the 0..duration window and the captions silently never appear.

    This writes a clip-relative copy of the subtitle file so ffmpeg renders the
    captions on the frame where they belong. Returns the path of the rebased copy.
    """
    sub_path = Path(subtitle_path)
    filename = sub_path.name

    rebased = sub_path.with_name(f"{sub_path.stem}.rel{sub_path.suffix}")

    def shift_ass_time(ts: str, offset: float) -> str:
        # ASS: h:mm:ss.cc (centiseconds)
        hh, mm, rest = ts.split(":")
        ss, cc = rest.split(".")
        secs = int(hh) * 3600 + int(mm) * 60 + int(ss) + int(cc) / 100.0
        secs = max(0.0, secs - offset)
        h = int(secs) // 3600
        m = (int(secs) // 60) % 60
        s = int(secs) % 60
        c = int(round((secs % 1) * 100))
        if c >= 100:
            c = 99
        return f"{h}:{m:02d}:{s:02d}.{c:02d}"

    def _shift_srt_time(stamp: str, offset: float) -> str:
        # SRT: hh:mm:ss,mmm
        hh, mm, rest = stamp.split(":")
        ss, ms = rest.split(",")
        secs = int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000.0
        secs = max(0.0, secs - offset)
        h = int(secs) // 3600
        m = (int(secs) // 60) % 60
        s = int(secs) % 60
        ms = int(round((secs % 1) * 1000))
        if ms >= 1000:
            ms = 999
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    try:
        text = sub_path.read_text(encoding="utf-8")
        if filename.lower().endswith(".ass"):
            out_lines = []
            for line in text.splitlines():
                if line.startswith("Dialogue:"):
                    parts = line.split(",", 9)
                    # The intro hook (Name field == "intro") is authored in
                    # clip-relative time (0..duration), so it must NOT be rebased
                    # by the clip offset. Without this guard, on any clip that
                    # doesn't start at 0 its 0..N window collapses to zero length
                    # (max(0, t - offset) == 0) and the hook silently never shows.
                    if len(parts) == 10 and parts[4].strip() != "intro":
                        parts[1] = shift_ass_time(parts[1], clip_offset)
                        parts[2] = shift_ass_time(parts[2], clip_offset)
                    line = ",".join(parts)
                out_lines.append(line)
            rebased.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
        elif filename.lower().endswith(".srt"):
            out_lines = []
            for line in text.splitlines():
                if "-->" in line:
                    m = re.match(r"^(\d+:\d\d:\d\d,\d+)\s*-->\s*(\d+:\d\d:\d\d,\d+)$", line.strip())
                    if m:
                        line = f"{_shift_srt_time(m.group(1), clip_offset)} --> {_shift_srt_time(m.group(2), clip_offset)}"
                out_lines.append(line)
            rebased.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
        else:
            # Unsupported subtitle type: fall back to the original.
            return subtitle_path
        return str(rebased)
    except Exception:
        # If anything goes wrong (e.g. exotic subtitle format) burn the original.
        return subtitle_path


def render_clip(
    input_video: str,
    output_video: str,
    start_time: float,
    end_time: float,
    aspect_ratio: Optional[str] = None,
    crop_x_offset: Optional[float] = None,
    burn_captions: bool = False,
    subtitle_path: Optional[str] = None,
    layout: str = "",
    cam_video: Optional[str] = None,
    cam_scale: float = 0.3,
    cam_position: str = "bottom-right",
    crop_x_expr: Optional[str] = None,
    # ---- audio/visual polish (all optional, off by default) ----
    normalize_audio: bool = False,
    music_path: Optional[str] = None,
    music_volume: float = 0.12,
    duck_music: bool = True,
    auto_zoom: bool = False,
    zoom_max: float = 1.08,
) -> str:
    """
    Render a clip segment with optional vertical crop and hardware-accelerated encoding.

    layout:
      '' | 'vertical'   -> 9:16 crop (legacy behaviour)
      'full'            -> no crop, full-frame passthrough
      'game_reaction'   -> full-frame gameplay + webcam reaction in a corner overlay
    """
    # Resolve before changing cwd below for the subtitles filter. A relative
    # output path would otherwise be resolved relative to the subtitle folder.
    output_video = str(Path(output_video).expanduser().resolve())
    input_video = str(Path(input_video).expanduser().resolve())
    if cam_video:
        cam_video = str(Path(cam_video).expanduser().resolve())
    Path(output_video).parent.mkdir(parents=True, exist_ok=True)
    duration = end_time - start_time

    has_cam = bool(cam_video and os.path.exists(cam_video))
    music_ok = bool(music_path and os.path.exists(str(Path(music_path).expanduser())))
    if music_ok:
        music_path = str(Path(music_path).expanduser().resolve())
    filters = build_filter_chain(
        aspect_ratio=aspect_ratio,
        crop_x_offset=crop_x_offset,
        layout=layout,
        has_cam=has_cam,
        cam_video=cam_video,
        cam_scale=cam_scale,
        cam_position=cam_position,
        crop_x_expr=crop_x_expr,
    )

    # Auto-zoom (gentle push-in). Needs concrete dimensions/fps for zoompan, so
    # probe the source; if that fails we simply skip the zoom (degrade quietly).
    if auto_zoom:
        dims = probe_video_dims(input_video)
        if dims:
            src_w, src_h, src_fps = dims
            if aspect_ratio == "9:16":
                zw, zh = int(src_h * 9 / 16), src_h
            elif aspect_ratio == "4:5":
                zw, zh = int(src_h * 4 / 5), src_h
            elif aspect_ratio == "1:1":
                zw = zh = min(src_w, src_h)
            else:
                zw, zh = src_w, src_h
            # Even dimensions keep H.264 happy.
            zw -= zw % 2
            zh -= zh % 2
            src_label = "[v]" if filters else "[0:v]"
            filters.append(build_zoompan_filter(
                src_label, "[v]", zw, zh, src_fps, zoom_max=zoom_max,
            ))

    use_complex = False

    burn_cwd = None
    if burn_captions and subtitle_path and os.path.exists(subtitle_path):
        # On Windows, ffmpeg's subtitles filter cannot parse a drive-letter path
        # (the "C:" colon is treated as an option separator and backslashes as
        # unknown options). The robust fix is to run ffmpeg with cwd = subtitle dir
        # and reference the subtitle by its bare relative filename. Any embedded
        # single quote in a filename is escaped with ffmpeg's '\\'' sequence.
        #
        # The subtitle file carries ABSOLUTE transcript timestamps while the
        # output timeline starts at 0 (input seek resets PTS), so rebase the
        # events to clip-local time before burning or captions land off-screen.
        subtitle_to_burn = _shift_subtitle_times(subtitle_path, start_time)
        sub_dir = os.path.dirname(os.path.abspath(subtitle_to_burn))
        sub_rel = os.path.basename(subtitle_to_burn).replace("'", "'\\''")
        burn_cwd = sub_dir
        if filters:
            # Consume [v] and emit a final [out] via a null passthrough so the
            # graph terminates cleanly with a single label.
            filters.append(f"[v]subtitles={sub_rel},null[out]")
        else:
            # Burn captions on a plain passthrough and label it [out].
            filters.append(f"[0:v]subtitles={sub_rel},null[out]")

    # Terminate the video graph with a single [out] label if any video filters ran.
    video_has_out = False
    if filters:
        video_has_out = any(part.endswith("[out]") for part in filters)
        if not video_has_out:
            filters.append("[v]null[out]")
            video_has_out = True

    # Audio graph (loudness normalization / background music / ducking).
    audio_chains, audio_out = build_audio_filter_chain(
        speech_label="0:a",
        music_label=(f"{2 if has_cam else 1}:a" if music_ok else None),
        normalize=normalize_audio,
        music_volume=music_volume,
        duck=duck_music,
    )

    graph = list(filters)
    if audio_out:
        graph += audio_chains
    use_complex = bool(graph)

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start_time),
        "-i", input_video,
    ]
    if has_cam:
        cmd += ["-i", cam_video]
    if music_ok:
        # Loop the music so a short track still covers the whole clip; the output
        # -t below bounds it (amix duration=first tracks the speech length).
        cmd += ["-stream_loop", "-1", "-i", music_path]
    cmd += ["-t", str(duration)]

    if use_complex:
        encoder, enc_args = detect_hw_encoder()
        cmd += ["-filter_complex", ";".join(graph)]
        if video_has_out:
            cmd += ["-map", "[out]", "-c:v", encoder, *enc_args]
            video_reencoded = True
        else:
            # Only audio was processed -> copy video untouched (fast, lossless).
            cmd += ["-map", "0:v:0", "-c:v", "copy"]
            video_reencoded = False
        cmd += ["-map", audio_out if audio_out else "0:a:0?"]
        cmd += ["-c:a", "aac", "-b:a", "192k", output_video]

        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=burn_cwd)
        if result.returncode != 0 and video_reencoded:
            # Fallback to software encoding libx264 (only when we actually
            # re-encoded video with the hardware encoder).
            cmd_fb = [c for c in cmd]
            enc_i = cmd_fb.index("-c:v")
            enc_args_i = enc_i + 2
            cmd_fb[enc_i + 1] = "libx264"
            cmd_fb[enc_args_i:enc_args_i + len(enc_args)] = X264_FALLBACK_ARGS
            res_fb = subprocess.run(cmd_fb, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=burn_cwd)
            if res_fb.returncode != 0:
                raise RuntimeError(f"Clip rendering failed: {res_fb.stderr[-500:]}")
        elif result.returncode != 0:
            raise RuntimeError(f"Clip rendering failed: {result.stderr[-500:]}")
        return output_video

    # Fast path: plain full-frame cut with no captions / PiP -> stream copy.
    # No re-encode, so manual trims are near-instant and lossless.
    cmd_copy = [
        "ffmpeg", "-y",
        "-ss", str(start_time),
        "-i", input_video,
        "-t", str(duration),
        "-map", "0:v:0", "-map", "0:a:0?",
        "-c:v", "copy", "-c:a", "copy",
        "-movflags", "+faststart",
        output_video,
    ]
    res_copy = subprocess.run(cmd_copy, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res_copy.returncode == 0 and os.path.getsize(output_video) > 0:
        return output_video

    # Fallback re-encode (e.g. stream-copy-incompatible source / weird container).
    encoder, enc_args = detect_hw_encoder()
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start_time),
        "-i", input_video,
        "-t", str(duration),
        "-map", "0:v:0", "-map", "0:a:0?",
        "-c:v", encoder,
        *enc_args,
        "-c:a", "aac", "-b:a", "192k",
        output_video,
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Clip rendering failed: {result.stderr[-500:]}")

    return output_video


def export_clip_as(
    src_path: str,
    out_path: str,
    fmt: str = "mp4",
) -> Tuple[str, str]:
    """
    Re-encode an existing rendered clip to a chosen container/codec:
      mp4  -> H.264/AAC (web-ready, GPU-accelerated if available)
      mov  -> H.264/AAC in QuickTime container
      mkv  -> H.264/AAC in Matroska container
      webm -> VP9/Opus (web-standard)
      av1  -> AV1 / Opus in MP4/MKV container (ultra high compression)
      gif  -> animated GIF from the video frames (palette-based, 15fps, max 720px wide)
    Returns (output_path, human-message).
    """
    fmt = fmt.lower().lstrip(".")
    if fmt not in ("mp4", "mov", "mkv", "webm", "av1", "gif"):
        raise ValueError(f"Unsupported export format: {fmt}")

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    out_suffix = Path(out_path).suffix.lower()
    if not out_suffix:
        suffix = "mp4" if fmt == "av1" else fmt
        out_path = f"{out_path}.{suffix}"

    if fmt == "gif":
        cmd = [
            "ffmpeg", "-y",
            "-i", src_path,
            "-vf", "fps=15,scale=w=min(iw\\,720):h=-2:force_original_aspect_ratio=decrease:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse",
            "-loop", "0",
            out_path,
        ]
    elif fmt == "av1":
        # AV1 export: attempt libsvtav1 (fast modern AV1 encoder) or fallback to libaom-av1
        cmd = [
            "ffmpeg", "-y",
            "-i", src_path,
            "-map", "0:v:0?", "-map", "0:a:0?",
            "-c:v", "libsvtav1", "-preset", "7", "-crf", "30", "-pix_fmt", "yuv420p10le",
            "-c:a", "libopus", "-b:a", "128k",
            out_path,
        ]
    else:
        encoder, enc_args = detect_hw_encoder()
        cmd = [
            "ffmpeg", "-y",
            "-i", src_path,
            "-map", "0:v:0?", "-map", "0:a:0?",
            "-c:v", encoder,
            *enc_args,
        ]
        if fmt == "webm":
            cmd += [
                "-c:v", "libvpx-vp9", "-b:v", "2M", "-crf", "32", "-deadline", "realtime",
                "-c:a", "libopus", "-b:a", "96k",
            ]
        else:
            cmd += ["-c:a", "aac", "-b:a", "192k"]
        if fmt in ("mp4", "mov", "mkv"):
            # faststart relocates the moov atom for instant web playback.
            # Harmless for mp4/mov; mkv stores cues separately, and this flag
            # does nothing useful there, so it is intentionally omitted.
            cmd += ["-movflags", "+faststart"] if fmt in ("mp4", "mov") else []
        cmd += [out_path]

    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0 and fmt == "av1":
        # Fallback to libaom-av1 if libsvtav1 is not compiled in FFmpeg
        cmd_aom = [
            "ffmpeg", "-y",
            "-i", src_path,
            "-map", "0:v:0?", "-map", "0:a:0?",
            "-c:v", "libaom-av1", "-crf", "32", "-cpu-used", "6",
            "-c:a", "libopus", "-b:a", "128k",
            out_path,
        ]
        result = subprocess.run(cmd_aom, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    elif result.returncode != 0 and fmt in ("mp4", "mov", "mkv"):
        # Hardware encoders (e.g. h264_nvenc) are detected from `ffmpeg -encoders`
        # but can still fail to open at runtime (driver/NVIDIA init, codec clash).
        # Retry the export once with software libx264 before giving up.
        cmd_fb = [c for c in cmd]
        enc_i = cmd_fb.index("-c:v")
        enc_args_i = enc_i + 2
        cmd_fb[enc_i + 1] = "libx264"
        cmd_fb[enc_args_i:enc_args_i + len(enc_args)] = list(X264_FALLBACK_ARGS)
        result = subprocess.run(cmd_fb, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Export to {fmt} failed: {result.stderr[-500:]}")

    return out_path, f"Exported as {fmt.upper()} successfully"


def extract_best_thumbnail(
    video_path: str,
    out_path: str,
    timestamp: float = 0.5,
) -> str:
    """
    Extract a high-quality JPEG thumbnail at a target timestamp (default 0.5s into the clip)
    for use as the video cover / poster image.
    """
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    if not out_path.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".bmp")):
        out_path = f"{out_path}.jpg"

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(max(0.0, timestamp)),
        "-i", video_path,
        "-vframes", "1",
        "-q:v", "2",
        out_path,
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        # Fallback to frame 0
        cmd_fb = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-vframes", "1",
            "-q:v", "2",
            out_path,
        ]
        subprocess.run(cmd_fb, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        raise RuntimeError(f"Thumbnail extraction failed for {video_path}")
    return out_path


def _score_frame(frame) -> float:
    """Score a BGR frame for thumbnail suitability: sharper + well-exposed is
    better. Uses the Laplacian variance (focus measure) scaled down when the
    frame is very dark or blown-out. Requires OpenCV; caller guards import."""
    import cv2
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    brightness = float(gray.mean())
    # Penalize near-black / blown-out frames (bad covers) but don't zero them.
    exposure = 1.0 if 30.0 <= brightness <= 225.0 else 0.35
    return sharpness * exposure


def extract_candidate_thumbnails(
    video_path: str,
    out_dir: str,
    count: int = 3,
    img_format: str = "jpg",
    sample: int = 15,
) -> List[Dict[str, Any]]:
    """Pick up to ``count`` strong candidate cover frames from a clip.

    Samples frames across the clip, scores them by sharpness/exposure (OpenCV),
    and returns the top spaced-out picks as saved images:
    ``[{"path", "timestamp", "score"}, ...]`` in chronological order. Falls back
    to evenly-spaced ffmpeg extractions (no scoring) when OpenCV can't decode.
    """
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    count = max(1, int(count))
    fmt = (img_format or "jpg").lower().lstrip(".")
    if fmt not in ("jpg", "jpeg", "png", "webp", "bmp"):
        fmt = "jpg"
    dur = get_video_duration(video_path) or 0.0

    # Preferred path: OpenCV sampling + scoring (opencv ships with the YOLO dep).
    try:
        import cv2
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise RuntimeError("OpenCV could not open the clip")
        # Sample across the middle of the clip to skip black intro/outro frames.
        lo, hi = (dur * 0.05, dur * 0.95) if dur > 1.0 else (0.0, max(0.0, dur))
        n = max(count, min(int(sample), 30))
        times = [lo + (hi - lo) * i / (n - 1) for i in range(n)] if n > 1 else [max(0.0, dur / 2)]
        scored = []
        for t in times:
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
            ok, frame = cap.read()
            if ok and frame is not None:
                scored.append((t, _score_frame(frame), frame))
        if not scored:
            cap.release()
            raise RuntimeError("No frames decoded")
        # Best score first, then greedily keep picks spaced apart in time so the
        # candidates look meaningfully different (not 3 near-identical frames).
        scored.sort(key=lambda x: x[1], reverse=True)
        min_gap = max(0.5, dur / (count * 2)) if dur > 0 else 0.5
        picked, picked_times = [], []
        for t, sc, frame in scored:
            if all(abs(t - pt) >= min_gap for pt in picked_times):
                picked.append((t, sc, frame)); picked_times.append(t)
            if len(picked) >= count:
                break
        if len(picked) < count:  # top up ignoring the spacing constraint
            for t, sc, frame in scored:
                if t in picked_times:
                    continue
                picked.append((t, sc, frame)); picked_times.append(t)
                if len(picked) >= count:
                    break
        picked.sort(key=lambda x: x[0])  # chronological order for display
        out: List[Dict[str, Any]] = []
        for i, (t, sc, frame) in enumerate(picked, 1):
            p = str(Path(out_dir) / f"thumb_{i}.{fmt}")
            try:
                if cv2.imwrite(p, frame) and os.path.exists(p) and os.path.getsize(p) > 0:
                    out.append({"path": p, "timestamp": round(float(t), 3), "score": round(float(sc), 2)})
            except Exception:
                continue
        cap.release()
        if out:
            return out
    except Exception:
        pass

    # Fallback: evenly-spaced stills via ffmpeg, no scoring.
    save_fmt = fmt if fmt in ("jpg", "jpeg", "png") else "jpg"
    times = [dur * (i + 1) / (count + 1) for i in range(count)] if dur > 0 else [0.5]
    out = []
    for i, t in enumerate(times, 1):
        p = str(Path(out_dir) / f"thumb_{i}.{save_fmt}")
        try:
            extract_best_thumbnail(video_path, p, timestamp=t)
            out.append({"path": p, "timestamp": round(float(t), 3), "score": 0.0})
        except Exception:
            continue
    return out


def concat_clips(
    clip_paths: List[str],
    out_path: str,
    fmt: str = "mp4",
) -> Tuple[str, str, float]:
    """
    Concatenate existing rendered clips into one highlights-reel media file
    using the safe concat demuxer (no re-encode of the clips themselves except
    format conversion when needed).
    Returns (out_path, message, total_duration).
    """
    fmt = fmt.lower().lstrip(".")
    if fmt not in ("mp4", "mov", "mkv", "webm", "av1", "gif"):
        raise ValueError(f"Unsupported export format: {fmt}")

    existing = [p for p in clip_paths if os.path.exists(p)]
    if not existing:
        raise FileNotFoundError("No existing clip files to concatenate")

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    if not Path(out_path).suffix:
        out_path = f"{out_path}.{fmt}"

    total_duration = sum(get_video_duration(p) for p in existing)

    # Build the concat list file (absolute POSIX-style paths, demuxer-safe).
    list_path = Path(out_path).with_suffix(".concat-list.txt")
    with open(list_path, "w", encoding="utf-8") as f:
        for p in existing:
            # ffmpeg concat demuxer wants paths escaped: single-quote-wrapped,
            # internal quotes doubled. Convert to forward slashes to be Windows-safe.
            p_safe = os.path.abspath(p).replace(os.sep, "/")
            f.write(f"file '{p_safe}'\n")

    try:
        if fmt == "gif":
            cmd = [
                "ffmpeg", "-y",
                "-f", "concat", "-safe", "0", "-i", str(list_path),
                "-vf", "fps=15,scale=w=min(iw\\,720):h=-2:force_original_aspect_ratio=decrease:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse",
                "-loop", "0",
                out_path,
            ]
        else:
            encoder, enc_args = detect_hw_encoder()
            cmd = [
                "ffmpeg", "-y",
                "-f", "concat", "-safe", "0", "-i", str(list_path),
                "-map", "0:v:0?", "-map", "0:a:0?",
                "-c:v", encoder,
                *enc_args,
            ]
            if fmt == "webm":
                cmd += [
                    "-c:v", "libvpx-vp9", "-b:v", "2M", "-crf", "32", "-deadline", "realtime",
                    "-c:a", "libopus", "-b:a", "96k",
                ]
            else:
                cmd += ["-c:a", "aac", "-b:a", "192k"]
            cmd += [out_path]

        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    finally:
        list_path.unlink(missing_ok=True)

    if result.returncode != 0 and fmt in ("mp4", "mov", "mkv"):
        # Restart once with software libx264 - hardware encoders (nvenc etc) can
        # detect successfully but still fail to open at runtime.
        cmd_fb = [c for c in cmd]
        enc_i = cmd_fb.index("-c:v")
        enc_args_i = enc_i + 2
        cmd_fb[enc_i + 1] = "libx264"
        fallback = list(X264_FALLBACK_ARGS)
        cmd_fb[enc_args_i:enc_args_i + len(enc_args)] = fallback
        result = subprocess.run(cmd_fb, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    if result.returncode != 0:
        raise RuntimeError(f"Reel export to {fmt} failed: {result.stderr[-120:]}")

    return out_path, f"Compiled {len(existing)} clips into a {fmt.upper()} reel successfully", total_duration


def generate_srt(segments: list, output_path: str) -> str:
    """Generate SRT subtitle file from transcript segments."""
    def fmt_time(seconds: float) -> str:
        ms = int((seconds % 1) * 1000)
        s = int(seconds) % 60
        m = (int(seconds) // 60) % 60
        h = int(seconds) // 3600
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    lines = []
    for i, seg in enumerate(segments, start=1):
        lines.append(str(i))
        lines.append(f"{fmt_time(seg.start)} --> {fmt_time(seg.end)}")
        lines.append(seg.text)
        lines.append("")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return output_path


def generate_vtt(segments: list, output_path: str) -> str:
    """Generate WebVTT subtitle file."""
    def fmt_time(seconds: float) -> str:
        ms = int((seconds % 1) * 1000)
        s = int(seconds) % 60
        m = (int(seconds) // 60) % 60
        h = int(seconds) // 3600
        return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"

    lines = ["WEBVTT", ""]
    for i, seg in enumerate(segments, start=1):
        lines.append(f"{fmt_time(seg.start)} --> {fmt_time(seg.end)}")
        lines.append(seg.text)
        lines.append("")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return output_path
