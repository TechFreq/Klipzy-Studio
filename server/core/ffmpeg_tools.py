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
) -> str:
    """Render a clip segment with optional vertical crop and hardware-accelerated encoding."""
    Path(output_video).parent.mkdir(parents=True, exist_ok=True)
    duration = end_time - start_time

    filters: List[str] = []

    if aspect_ratio == "9:16":
        if crop_x_offset is not None:
            filters.append(f"crop=ih*9/16:ih:{crop_x_offset}:0")
        else:
            filters.append("crop=ih*9/16:ih:(iw-ow)/2:0")

    if burn_captions and subtitle_path and os.path.exists(subtitle_path):
        clean_sub = subtitle_path.replace("\\", "/").replace(":", "\\:")
        filters.append(f"subtitles='{clean_sub}'")

    encoder, enc_args = detect_hw_encoder()

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start_time),
        "-i", input_video,
        "-t", str(duration),
    ]

    if filters:
        cmd += ["-vf", ",".join(filters)]

    cmd += [
        "-c:v", encoder,
        *enc_args,
        "-c:a", "aac", "-b:a", "192k",
        output_video
    ]

    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        # Fallback to software encoding libx264
        cmd_fb = [
            "ffmpeg", "-y",
            "-ss", str(start_time),
            "-i", input_video,
            "-t", str(duration),
        ]
        if filters:
            cmd_fb += ["-vf", ",".join(filters)]
        cmd_fb += [
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            "-c:a", "aac", "-b:a", "192k",
            output_video
        ]
        res_fb = subprocess.run(cmd_fb, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
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