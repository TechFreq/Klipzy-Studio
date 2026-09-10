"""
Whisper model presence + download, decoupled from transcription.

Why this exists
---------------
Model download used to be implicit: the first time a size was used, the
transcription backend fetched it from the Hugging Face Hub *inside* transcribe(),
under the single "Transcribing..." progress step. On a fresh model that meant the
run sat at ~25% for the length of a multi-GB download with no indication it was
downloading rather than stuck.

This module lets the app do what the Ollama model catalog already does for LLMs:
check whether the selected model is present *before* a job starts, and if not,
download it as its own visible step with its own progress — then transcribe.

Backend awareness
-----------------
The transcriber picks a backend at runtime (remote endpoint -> mlx on Apple
Silicon -> faster-whisper -> openai-whisper). The model artifact and its cache
location differ per backend, so presence and download are resolved against
whichever backend detect_active_backend() says WOULD run:

  * remote  -> nothing to download locally (present is always True)
  * faster-whisper / mlx -> Hugging Face snapshot (huggingface_hub cache)
  * openai-whisper -> single .pt file in ~/.cache/whisper

Progress is reported as (completed_bytes, total_bytes, percent). For the HF
snapshot path the big weight file (model.bin) dominates, so the percentage is
meaningful even though a few tiny config files are downloaded alongside it.
"""

import os
import threading
from typing import Callable, Optional

# faster-whisper's download_model uses exactly these patterns; matching them
# keeps our presence check and download in step with what transcribe() loads.
_FASTER_ALLOW_PATTERNS = [
    "config.json",
    "preprocessor_config.json",
    "model.bin",
    "tokenizer.json",
    "vocabulary.*",
]

# Mirror of the mlx repo mapping in Transcriber._load_model().
_MLX_REPOS = {
    "tiny": "mlx-community/whisper-tiny-mlx",
    "base": "mlx-community/whisper-base-mlx",
    "small": "mlx-community/whisper-small-mlx",
    "medium": "mlx-community/whisper-medium-mlx",
    "large": "mlx-community/whisper-large-v3-mlx",
    "large-v2": "mlx-community/whisper-large-v2-mlx",
    "large-v3": "mlx-community/whisper-large-v3-mlx",
}


class DownloadCancelled(Exception):
    """Raised inside a download when the caller asks to cancel it."""


ProgressCb = Callable[[int, int, float], None]
CancelCb = Callable[[], bool]


def active_backend() -> Optional[str]:
    """Which backend WOULD transcribe, normalized. One of:
    'remote' | 'mlx' | 'faster-whisper' | 'openai-whisper' | None (none installed).
    """
    try:
        from server.core.transcriber import detect_active_backend
        return detect_active_backend().get("active")
    except Exception:
        return None


# ----------------------------------------------------------------------
# Presence
# ----------------------------------------------------------------------
def _openai_cache_root() -> str:
    return os.path.join(
        os.getenv("XDG_CACHE_HOME", os.path.join(os.path.expanduser("~"), ".cache")),
        "whisper",
    )


def _openai_present(model_size: str) -> bool:
    try:
        import whisper
        url = whisper._MODELS.get(model_size)
        if not url:
            return False
        target = os.path.join(_openai_cache_root(), os.path.basename(url))
        return os.path.isfile(target) and os.path.getsize(target) > 0
    except Exception:
        return False


def _hf_repo(model_size: str, backend: str) -> Optional[str]:
    if backend == "mlx":
        if "/" in model_size or "\\" in model_size:
            return model_size
        return _MLX_REPOS.get(model_size, f"mlx-community/whisper-{model_size}-mlx")
    # faster-whisper
    try:
        from faster_whisper.utils import _MODELS
        if "/" in model_size:
            return model_size
        return _MODELS.get(model_size)
    except Exception:
        return None


def _hf_present(model_size: str, backend: str) -> bool:
    if backend == "faster-whisper":
        # Reuse faster-whisper's own resolver so "present" means exactly what it
        # would load — raises if any required file is missing from the cache.
        try:
            from faster_whisper.utils import download_model
            download_model(model_size, local_files_only=True)
            return True
        except Exception:
            return False
    # mlx (or any HF-repo path)
    repo = _hf_repo(model_size, backend)
    if not repo:
        return False
    try:
        from huggingface_hub import snapshot_download
        snapshot_download(repo, local_files_only=True)
        return True
    except Exception:
        return False


def status(model_size: str) -> dict:
    """Report whether `model_size` is ready to transcribe with, and whether it
    even needs a local download for the active backend.

    Returns keys: present (bool), backend (str|None), downloadable (bool),
    repo (str|None). `downloadable` is False when there's nothing to pre-fetch
    (a remote endpoint is configured, or no backend is installed at all)."""
    model_size = (model_size or "base").strip()
    backend = active_backend()

    if backend == "remote":
        return {"present": True, "backend": backend, "downloadable": False, "repo": None}
    if backend is None:
        # No local transcription backend installed. A model download can't help;
        # the deps panel is where the user installs a backend. Don't block a run.
        return {"present": True, "backend": None, "downloadable": False, "repo": None}

    if backend == "openai-whisper":
        return {
            "present": _openai_present(model_size),
            "backend": backend,
            "downloadable": True,
            "repo": None,
        }

    # faster-whisper / mlx
    return {
        "present": _hf_present(model_size, backend),
        "backend": backend,
        "downloadable": True,
        "repo": _hf_repo(model_size, backend),
    }


# ----------------------------------------------------------------------
# Download (with byte progress + cooperative cancel)
# ----------------------------------------------------------------------
class _NullSink:
    """Swallows tqdm's console output. We can't use tqdm(disable=True) because a
    disabled bar's update() is a no-op that never advances .n, so we'd lose the
    byte count. Redirecting the display to a sink keeps .n/.total tracking intact
    (as huggingface_hub sets them) while writing nothing to the console."""

    def write(self, *_args, **_kwargs):
        return 0

    def flush(self):
        pass


def _make_tqdm(progress: ProgressCb, should_cancel: Optional[CancelCb]):
    """Build a tqdm subclass huggingface_hub can use, which forwards aggregate
    byte progress to `progress` and aborts the download when `should_cancel`
    returns True.

    huggingface_hub creates several bars: a cumulative "Downloading bytes" bar
    (its .total is filled in after construction as file sizes are discovered),
    plus a small "Fetching N files" count bar. We only aggregate the byte bars
    (unit == 'B'); the file-count bar is ignored so percent reflects real bytes.
    """
    import tqdm as _tqdm_mod

    files: dict = {}
    lock = threading.Lock()

    def emit():
        with lock:
            done = sum(v[0] for v in files.values())
            total = sum(v[1] for v in files.values())
        pct = round(100.0 * done / total, 1) if total else 0.0
        try:
            progress(done, total, pct)
        except Exception:
            pass

    class _CallbackTqdm(_tqdm_mod.tqdm):
        def __init__(self, *args, **kwargs):
            kwargs.setdefault("file", _NullSink())  # suppress console output
            super().__init__(*args, **kwargs)
            # Only byte bars count toward the download percentage.
            self._is_bytes = getattr(self, "unit", None) == "B"
            if self._is_bytes:
                with lock:
                    files[id(self)] = [int(self.n or 0), int(self.total or 0)]
                emit()

        def update(self, n=1):
            ret = super().update(n)
            if self._is_bytes:
                with lock:
                    slot = files.get(id(self))
                    if slot is not None:
                        slot[0] = int(self.n)
                        if self.total:
                            slot[1] = int(self.total)
                emit()
            if should_cancel and should_cancel():
                raise DownloadCancelled()
            return ret

    return _CallbackTqdm


def _download_hf(model_size: str, backend: str, progress: ProgressCb,
                 should_cancel: Optional[CancelCb]) -> str:
    import huggingface_hub

    repo = _hf_repo(model_size, backend)
    if not repo:
        raise ValueError(f"Unknown model '{model_size}' for backend {backend}")
    kwargs = {"tqdm_class": _make_tqdm(progress, should_cancel)}
    if backend == "faster-whisper":
        kwargs["allow_patterns"] = _FASTER_ALLOW_PATTERNS
    return huggingface_hub.snapshot_download(repo, **kwargs)


def _download_openai(model_size: str, progress: ProgressCb,
                     should_cancel: Optional[CancelCb]) -> str:
    """Stream the single .pt weight file, reporting byte progress. Mirrors
    whisper._download but with a progress callback and cancel support."""
    import urllib.request

    import whisper

    url = whisper._MODELS.get(model_size)
    if not url:
        raise ValueError(f"Unknown openai-whisper model '{model_size}'")
    root = _openai_cache_root()
    os.makedirs(root, exist_ok=True)
    target = os.path.join(root, os.path.basename(url))
    tmp = target + ".part"

    with urllib.request.urlopen(url) as source, open(tmp, "wb") as out:
        total = int(source.info().get("Content-Length") or 0)
        done = 0
        progress(0, total, 0.0)
        while True:
            if should_cancel and should_cancel():
                raise DownloadCancelled()
            buf = source.read(1024 * 256)
            if not buf:
                break
            out.write(buf)
            done += len(buf)
            pct = round(100.0 * done / total, 1) if total else 0.0
            progress(done, total, pct)

    os.replace(tmp, target)
    return target


def download(model_size: str, progress: ProgressCb,
             should_cancel: Optional[CancelCb] = None) -> str:
    """Download `model_size` for the active backend. Blocking; run it in a
    thread. Raises DownloadCancelled if `should_cancel` fires, and cleans up any
    partial openai .pt file. HF snapshots resume automatically on the next try,
    so a half-download there is not left in a broken state."""
    model_size = (model_size or "base").strip()
    backend = active_backend()
    try:
        if backend == "openai-whisper":
            return _download_openai(model_size, progress, should_cancel)
        if backend in ("faster-whisper", "mlx"):
            return _download_hf(model_size, backend, progress, should_cancel)
        raise RuntimeError(f"No downloadable local backend (active={backend})")
    except DownloadCancelled:
        if backend == "openai-whisper":
            try:
                import whisper
                url = whisper._MODELS.get(model_size)
                if url:
                    part = os.path.join(_openai_cache_root(), os.path.basename(url) + ".part")
                    if os.path.isfile(part):
                        os.remove(part)
            except Exception:
                pass
        raise
