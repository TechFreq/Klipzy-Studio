"""Compatibility re-export shim for the legacy `src.ffmpeg_tools` import path."""

from server.core.ffmpeg_tools import (  # noqa: F401
    check_ffmpeg,
    get_media_info,
    get_video_duration,
    detect_hw_encoder,
    extract_audio,
    render_clip,
    generate_srt,
    generate_vtt,
)

__all__ = [
    "check_ffmpeg",
    "get_media_info",
    "get_video_duration",
    "detect_hw_encoder",
    "extract_audio",
    "render_clip",
    "generate_srt",
    "generate_vtt",
]