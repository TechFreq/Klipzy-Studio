"""
Silence & dead-air auto-cutter using FFmpeg silencedetect.
Removes awkward pauses, stream dead air, and silence gaps to create fast-paced viral clips.
"""

import re
import os
import subprocess  # kept for subprocess.PIPE
from pathlib import Path
from typing import List, Dict, Tuple, Any, Optional
from server.core.ffmpeg_tools import get_video_duration, detect_hw_encoder, X264_FALLBACK_ARGS
from server.core import proc  # killable subprocess runner (job cancel)


def detect_silence_intervals(
    media_path: str,
    noise_threshold_db: float = -30.0,
    min_silence_duration: float = 0.6,
) -> List[Dict[str, float]]:
    """
    Detects silent gaps in audio using FFmpeg silencedetect filter.
    Returns list of dicts: [{"start": 1.2, "end": 2.5, "duration": 1.3}, ...]
    """
    if not os.path.exists(media_path):
        raise FileNotFoundError(f"Media file not found: {media_path}")

    cmd = [
        "ffmpeg", "-i", media_path,
        "-af", f"silencedetect=noise={noise_threshold_db}dB:d={min_silence_duration}",
        "-f", "null", "-"
    ]
    res = proc.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    stderr = res.stderr

    silence_starts = []
    intervals = []

    for line in stderr.splitlines():
        if "silence_start:" in line:
            m = re.search(r"silence_start:\s*([0-9.]+)", line)
            if m:
                silence_starts.append(float(m.group(1)))
        elif "silence_end:" in line:
            m_end = re.search(r"silence_end:\s*([0-9.]+)", line)
            m_dur = re.search(r"silence_duration:\s*([0-9.]+)", line)
            if m_end and silence_starts:
                start = silence_starts.pop(0)
                end = float(m_end.group(1))
                dur = float(m_dur.group(1)) if m_dur else (end - start)
                intervals.append({
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "duration": round(dur, 3),
                })

    return intervals


def calculate_speech_segments(
    total_duration: float,
    silence_intervals: List[Dict[str, float]],
    pad_seconds: float = 0.08,
) -> List[Tuple[float, float]]:
    """
    Inverts silence intervals into speech intervals to retain, with padding to avoid clipping consonants.
    """
    if not silence_intervals:
        return [(0.0, total_duration)]

    speech_segments = []
    cur_t = 0.0

    for sil in silence_intervals:
        sil_start = max(0.0, sil["start"] + pad_seconds)
        sil_end = min(total_duration, sil["end"] - pad_seconds)

        if sil_start > cur_t:
            speech_segments.append((round(cur_t, 3), round(sil_start, 3)))
        cur_t = max(cur_t, sil_end)

    if cur_t < total_duration:
        speech_segments.append((round(cur_t, 3), round(total_duration, 3)))

    # Filter out empty or negligible segments (<0.1s)
    valid = [(s, e) for s, e in speech_segments if (e - s) >= 0.1]
    return valid or [(0.0, total_duration)]


def remove_silence(
    input_video: str,
    output_video: str,
    noise_threshold_db: float = -30.0,
    min_silence_duration: float = 0.6,
    pad_seconds: float = 0.10,
) -> Dict[str, Any]:
    """
    Detects and cuts out dead air silence from a video clip.
    Returns metadata with original duration, cut duration, and silence saved.
    """
    orig_dur = get_video_duration(input_video)
    if orig_dur <= 0:
        raise ValueError(f"Invalid duration for video: {input_video}")

    silences = detect_silence_intervals(
        input_video,
        noise_threshold_db=noise_threshold_db,
        min_silence_duration=min_silence_duration,
    )

    if not silences:
        # No dead air found, copy directly
        Path(output_video).parent.mkdir(parents=True, exist_ok=True)
        import shutil
        shutil.copy2(input_video, output_video)
        return {
            "output_path": output_video,
            "original_duration": round(orig_dur, 2),
            "cut_duration": round(orig_dur, 2),
            "time_saved": 0.0,
            "silence_intervals": [],
            "message": "No dead air detected exceeding threshold."
        }

    speech_segs = calculate_speech_segments(orig_dur, silences, pad_seconds=pad_seconds)
    
    # Build select and aselect filters for FFmpeg
    v_expr_parts = [f"between(t,{s},{e})" for s, e in speech_segs]
    v_expr = "+".join(v_expr_parts)
    a_expr = v_expr

    filter_complex = (
        f"[0:v]select='{v_expr}',setpts=N/FRAME_RATE/TB[v];"
        f"[0:a]aselect='{a_expr}',asetpts=N/SR/TB[a]"
    )

    encoder, enc_args = detect_hw_encoder()
    Path(output_video).parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg", "-y",
        "-i", input_video,
        "-filter_complex", filter_complex,
        "-map", "[v]", "-map", "[a]",
        "-c:v", encoder,
        *enc_args,
        "-c:a", "aac", "-b:a", "192k",
        output_video
    ]

    res = proc.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        # Fallback to libx264 if hardware encoder fails filter_complex
        cmd_fallback = [
            "ffmpeg", "-y",
            "-i", input_video,
            "-filter_complex", filter_complex,
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", *X264_FALLBACK_ARGS,
            "-c:a", "aac", "-b:a", "192k",
            output_video
        ]
        res_fb = proc.run(cmd_fallback, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res_fb.returncode != 0:
            raise RuntimeError(f"Silence removal failed: {res_fb.stderr[-500:]}")

    cut_dur = get_video_duration(output_video)
    saved = max(0.0, round(orig_dur - cut_dur, 2))

    return {
        "output_path": output_video,
        "original_duration": round(orig_dur, 2),
        "cut_duration": round(cut_dur, 2),
        "time_saved": saved,
        "silence_intervals": silences,
        "segments_kept": len(speech_segs),
        "message": f"Successfully removed {saved}s of dead air."
    }


def render_kept_segments(input_video: str, output_video: str, speech_segs: List[Tuple[float, float]]) -> str:
    """Render only the given keep-segments of a video into output_video, dropping
    everything else (used by both silence removal and filler-word removal).
    """
    if not speech_segs:
        import shutil
        Path(output_video).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(input_video, output_video)
        return output_video

    v_expr = "+".join(f"between(t,{s},{e})" for s, e in speech_segs)
    filter_complex = (
        f"[0:v]select='{v_expr}',setpts=N/FRAME_RATE/TB[v];"
        f"[0:a]aselect='{v_expr}',asetpts=N/SR/TB[a]"
    )
    encoder, enc_args = detect_hw_encoder()
    Path(output_video).parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-i", input_video,
        "-filter_complex", filter_complex,
        "-map", "[v]", "-map", "[a]",
        "-c:v", encoder, *enc_args,
        "-c:a", "aac", "-b:a", "192k",
        output_video,
    ]
    res = proc.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        cmd_fb = [
            "ffmpeg", "-y", "-i", input_video,
            "-filter_complex", filter_complex,
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", *X264_FALLBACK_ARGS,
            "-c:a", "aac", "-b:a", "192k",
            output_video,
        ]
        res_fb = proc.run(cmd_fb, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res_fb.returncode != 0:
            raise RuntimeError(f"Segment render failed: {res_fb.stderr[-500:]}")
    return output_video
