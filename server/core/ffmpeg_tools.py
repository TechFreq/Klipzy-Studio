"""
FFmpeg wrapper - cross-platform video/audio manipulation.
Works on Windows and macOS. Requires ffmpeg/ffprobe in PATH.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple


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
    Returns (encoder_name, extra_args).
    """
    import platform
    system = platform.system()
    try:
        res = subprocess.run(["ffmpeg", "-encoders"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        stdout = res.stdout

        if "h264_nvenc" in stdout:
            return "h264_nvenc", ["-preset", "p4", "-cq", "23"]
        elif system == "Darwin" and "h264_videotoolbox" in stdout:
            return "h264_videotoolbox", ["-q:v", "65"]
        elif "h264_vaapi" in stdout:
            return "h264_vaapi", []
    except Exception:
        pass

    return "libx264", ["-preset", "fast", "-crf", "22"]


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
) -> str:
    """
    Render a clip segment with optional vertical crop and hardware-accelerated encoding.

    layout:
      '' | 'vertical'   -> 9:16 crop (legacy behaviour)
      'full'            -> no crop, full-frame passthrough
      'game_reaction'   -> full-frame gameplay + webcam reaction in a corner overlay
    """
    Path(output_video).parent.mkdir(parents=True, exist_ok=True)
    duration = end_time - start_time

    has_cam = bool(cam_video and os.path.exists(cam_video))
    filters: List[str] = []
    use_complex = False

    if layout == "game_reaction":
        if has_cam:
            use_complex = True
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
            # Output is threaded to label [v] so later chain steps can consume it.
            base_chain = (
                f"[1:v]scale={cam_w}:{cam_h}:force_original_aspect_ratio=decrease,"
                f"pad={cam_w}:{cam_h}:(ow-iw)/2:(oh-ih)/2:color=0x000000,"
                f"pad={box_w}:{box_h}:{pad}:{pad}:color=0xFFFFFF,setsar=1[cf];"
                f"[0:v][cf]overlay={x}:{y}:eof_action=repeat[v]"
            )
            filters.append(base_chain)
    elif aspect_ratio == "9:16":
        if crop_x_offset is not None:
            filters.append(f"[0:v]crop=ih*9/16:ih:{crop_x_offset}:0[v]")
        else:
            filters.append("[0:v]crop=ih*9/16:ih:(iw-ow)/2:0[v]")

    burn_cwd = None
    if burn_captions and subtitle_path and os.path.exists(subtitle_path):
        # On Windows, ffmpeg's subtitles filter cannot parse a drive-letter path
        # (the "C:" colon is treated as an option separator and backslashes as
        # unknown options). The robust fix is to run ffmpeg with cwd = subtitle dir
        # and reference the subtitle by its bare relative filename. Any embedded
        # single quote in a filename is escaped with ffmpeg's '\\'' sequence.
        sub_dir = os.path.dirname(os.path.abspath(subtitle_path))
        sub_rel = os.path.basename(subtitle_path).replace("'", "'\\''")
        burn_cwd = sub_dir
        if filters:
            # Consume [v] and emit a final [out] via a null passthrough so the
            # graph terminates cleanly with a single label.
            filters.append(f"[v]subtitles={sub_rel},null[out]")
        else:
            # Burn captions on a plain passthrough and label it [out].
            filters.append(f"[0:v]subtitles={sub_rel},null[out]")

    # If filters were built, ensure the graph terminates with a single [out] label.
    if filters:
        has_out = any(part.endswith("[out]") for part in filters)
        if not has_out:
            filters.append("[v]null[out]")
        use_complex = True

    encoder, enc_args = detect_hw_encoder()

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start_time),
        "-i", input_video,
    ]
    if has_cam:
        cmd += ["-i", cam_video]
    cmd += ["-t", str(duration)]

    if use_complex:
        cmd += ["-filter_complex", ";".join(filters)]
        cmd += ["-map", "[out]", "-map", "0:a:0?"]
    cmd += [
        "-c:v", encoder,
        *enc_args,
        "-c:a", "aac", "-b:a", "192k",
        output_video
    ]

    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=burn_cwd)
    if result.returncode != 0:
        # Fallback to software encoding libx264
        cmd_fb = [c for c in cmd]
        enc_i = cmd_fb.index("-c:v")
        enc_args_i = enc_i + 2
        cmd_fb[enc_i + 1] = "libx264"
        cmd_fb[enc_args_i:enc_args_i + len(enc_args)] = ["-preset", "fast", "-crf", "22"]
        res_fb = subprocess.run(cmd_fb, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=burn_cwd)
        if res_fb.returncode != 0:
            raise RuntimeError(f"Clip rendering failed: {res_fb.stderr[-500:]}")

    return output_video


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