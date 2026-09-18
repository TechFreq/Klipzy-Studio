"""Bounded, cached editor thumbnails and source-audio peaks; never alters media."""
import base64
import json
import math
import os
from array import array
from functools import lru_cache
import subprocess


def _run(args, timeout=30):
    return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=timeout,
                          creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0).stdout


def audio_peaks(raw, bins=240):
    samples = array("h")
    samples.frombytes(raw[:len(raw) // 2 * 2])
    if not samples:
        return []
    step = max(1, math.ceil(len(samples) / bins))
    return [round(max(abs(value) for value in samples[i:i+step]) / 32768, 4)
            for i in range(0, len(samples), step)]


def timeline_media(path, start, duration):
    stat = os.stat(path)
    return _cached(os.path.abspath(path), stat.st_mtime_ns, stat.st_size, round(start, 3), round(duration, 3))


@lru_cache(maxsize=8)
def _cached(path, mtime, size, start, duration):
    probe = json.loads(_run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", path], 10))
    streams = probe.get("streams", [])
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    if not video:
        raise ValueError("No video stream found")
    num, den = (video.get("avg_frame_rate") or "30/1").split("/")
    fps = float(num) / float(den) if float(den) else 30.0
    frames = []
    # A single decode pass with a sparse output rate, capped at eight small JPEGs.
    raw = _run(["ffmpeg", "-v", "error", "-ss", str(start), "-i", path, "-t", str(duration),
                "-an", "-vf", f"fps={8/duration},scale=160:90:force_original_aspect_ratio=decrease,pad=160:90:(ow-iw)/2:(oh-ih)/2",
                "-frames:v", "8", "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"], 60)
    for chunk in raw.split(b"\xff\xd9"):
        begin = chunk.find(b"\xff\xd8")
        if begin >= 0:
            frames.append("data:image/jpeg;base64," + base64.b64encode(chunk[begin:] + b"\xff\xd9").decode("ascii"))
    peaks = []
    if any(stream.get("codec_type") == "audio" for stream in streams):
        raw = _run(["ffmpeg", "-v", "error", "-ss", str(start), "-i", path, "-t", str(duration),
                    "-vn", "-ac", "1", "-ar", "2000", "-f", "s16le", "pipe:1"], 60)
        peaks = audio_peaks(raw)
    return {"thumbnails": frames, "peaks": peaks, "fps": fps if fps > 0 else 30, "duration": duration}
