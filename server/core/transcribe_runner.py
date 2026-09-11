"""
Cancellable transcription entry point for the pipeline.

Runs the transcription worker (server.core.transcribe_worker) as a child process
through proc.run, so a job cancel hard-kills it (fixing the handoff §5.2 limit
that Whisper couldn't be interrupted once started). Falls back to in-process
transcription whenever the subprocess path isn't viable (frozen/packaged app, a
launch error, or a non-cancel worker failure) so behaviour is never worse than
before — only more cancellable.

Cancellation surfaces as proc.CancelledError, exactly like a killed ffmpeg, so
the pipeline's existing cancel handling unwinds it unchanged.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Optional

from server.core import proc
from server.models import TranscriptSegment


def _in_process(audio_path: str, model_size: str, language: Optional[str]) -> List[TranscriptSegment]:
    from server.core.transcriber import Transcriber
    return Transcriber(model_size=model_size).transcribe(audio_path, language=language)


def _subprocess_viable() -> bool:
    """Can we spawn `python -m server.core.transcribe_worker`? Not in a frozen
    bundle (PyInstaller ignores -m), and an explicit override can force in-proc."""
    if os.environ.get("KLIPZY_INPROCESS_TRANSCRIBE", "").strip().lower() in ("1", "true", "yes"):
        return False
    if getattr(sys, "frozen", False):
        return False
    return bool(sys.executable)


def _safe_remove(path: str) -> None:
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def transcribe_cancellable(
    audio_path: str, model_size: str = "base", language: Optional[str] = None,
) -> List[TranscriptSegment]:
    """Transcribe `audio_path`, cancellable via proc.request_cancel().

    Tries the killable child process first; on cancel it re-raises
    proc.CancelledError, and on any non-cancel problem it falls back to
    in-process transcription (which has its own multi-backend fallbacks)."""
    if not _subprocess_viable():
        return _in_process(audio_path, model_size, language)

    fd, out_json = tempfile.mkstemp(suffix=".klipzy-transcript.json")
    os.close(fd)
    cmd = [
        sys.executable, "-m", "server.core.transcribe_worker",
        audio_path, "--model", model_size, "--out", out_json,
    ]
    if language:
        cmd += ["--language", language]

    # Run from the repo root so `-m server.core...` resolves regardless of cwd.
    repo_root = str(Path(__file__).resolve().parents[2])
    try:
        result = proc.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=repo_root)
    except proc.CancelledError:
        _safe_remove(out_json)
        raise
    except Exception:
        # Couldn't even launch the child (unusual) — fall back in-process.
        _safe_remove(out_json)
        return _in_process(audio_path, model_size, language)

    if result.returncode != 0:
        # The worker failed for a non-cancel reason; the in-process transcriber
        # is the robust backstop (it degrades across backends itself).
        _safe_remove(out_json)
        return _in_process(audio_path, model_size, language)

    try:
        data = json.loads(Path(out_json).read_text(encoding="utf-8"))
        return [TranscriptSegment(**s) for s in data]
    except Exception:
        _safe_remove(out_json)
        return _in_process(audio_path, model_size, language)
    finally:
        _safe_remove(out_json)
