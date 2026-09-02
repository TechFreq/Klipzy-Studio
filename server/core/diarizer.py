"""
Speaker diarization ("who spoke when") — OPTIONAL.

⚠️ UNTESTED / OPTIONAL: this uses pyannote.audio, a heavy extra dependency that
also needs a Hugging Face token and a model download. It is NOT installed by
default and was written without hardware to test it on. Everything degrades
gracefully: if pyannote (or a token/model) is missing, diarization_available()
returns False and callers should hide/disable the feature rather than error.
"""

import os
from typing import Any, Dict, List, Optional


def diarization_available() -> bool:
    """True only if pyannote.audio is importable. (A working run also needs a
    HUGGINGFACE token + the pretrained pipeline downloaded.)"""
    try:
        import pyannote.audio  # noqa: F401
        return True
    except Exception:
        return False


def diarize(audio_or_video_path: str, hf_token: Optional[str] = None) -> Dict[str, Any]:
    """Return {"available": bool, "segments": [{start,end,speaker}], "message": str}.

    Never raises for the common "not installed / no token" cases — the caller
    can surface the message and offer the install instead.
    """
    if not diarization_available():
        return {
            "available": False,
            "segments": [],
            "message": "Speaker diarization needs the optional 'pyannote.audio' package. "
                       "Install it and set a Hugging Face token to enable it.",
        }
    token = hf_token or os.environ.get("HUGGINGFACE_TOKEN") or os.environ.get("HF_TOKEN")
    if not token:
        return {
            "available": False,
            "segments": [],
            "message": "pyannote is installed, but a Hugging Face token is required "
                       "(set HF_TOKEN) to download the diarization model.",
        }
    try:
        from pyannote.audio import Pipeline
        pipeline = Pipeline.from_pretrained(
            "pyannote/speaker-diarization-3.1", use_auth_token=token
        )
        annotation = pipeline(audio_or_video_path)
        segments: List[Dict[str, Any]] = []
        for turn, _, speaker in annotation.itertracks(yield_label=True):
            segments.append({
                "start": round(float(turn.start), 3),
                "end": round(float(turn.end), 3),
                "speaker": str(speaker),
            })
        speakers = sorted({s["speaker"] for s in segments})
        return {
            "available": True,
            "segments": segments,
            "speakers": speakers,
            "message": f"Detected {len(speakers)} speaker(s) across {len(segments)} turns.",
        }
    except Exception as e:  # noqa: BLE001
        return {"available": False, "segments": [], "message": f"Diarization failed: {e}"}
