"""
Transcription module with multiple backends for cross-platform acceleration:
  1. MLX-Whisper (Apple Silicon macOS) - native MLX acceleration, fastest on M-series
  2. Faster-Whisper (CTranslate2) - 3-5x faster on CPU/GPU, low memory
  3. OpenAI Whisper (PyTorch) - fallback with CUDA/MPS/CPU support

All backends produce word-level timestamps for captions / energy / word-filter features.
"""

import os
import sys
from typing import List, Optional

from server.models import TranscriptSegment, WordTimestamp


def _is_apple_silicon() -> bool:
    """Check if running on macOS Apple Silicon (M-series)."""
    return sys.platform == "darwin" and hasattr(os, "uname") and os.uname().machine == "arm64"


def detect_active_backend() -> dict:
    """
    Report which transcription backend WOULD be used, without loading a model.

    Mirrors the priority in Transcriber._load_model():
      mlx (Apple Silicon only) -> faster-whisper -> openai-whisper.
    Cheap enough (import spec checks) to call from /health.
    """
    import importlib.util as _u

    apple_silicon = _is_apple_silicon()
    have_mlx = _u.find_spec("mlx_whisper") is not None
    have_faster = _u.find_spec("faster_whisper") is not None
    have_openai = _u.find_spec("whisper") is not None

    available = []
    if apple_silicon and have_mlx:
        available.append("mlx")
    if have_faster:
        available.append("faster-whisper")
    if have_openai:
        available.append("openai-whisper")

    # A configured remote endpoint takes priority, mirroring _load_model(). It
    # can't be probed with find_spec (it's an HTTP service), so report it as
    # active whenever it's configured and note if it isn't answering.
    try:
        from server.core import asr_client
        if asr_client.is_remote():
            cfg = asr_client.load_config()
            reachable = asr_client.is_available(cfg)
            available.insert(0, "remote")
            return {
                "active": "remote",
                "available": available,
                "apple_silicon": apple_silicon,
                "note": (f"Remote endpoint {cfg['base_url']}"
                         + ("" if reachable else " (not answering — will fall back locally)")),
            }
    except Exception:
        pass

    if apple_silicon and have_mlx:
        active, note = "mlx", "MLX native acceleration (Apple Silicon)"
    elif have_faster:
        active, note = "faster-whisper", "CTranslate2 (3-5x faster than openai-whisper)"
    elif have_openai:
        active, note = "openai-whisper", "PyTorch fallback (CUDA/MPS/CPU)"
    else:
        active, note = None, "No transcription backend installed"

    return {
        "active": active,
        "available": available,
        "apple_silicon": apple_silicon,
        "note": note,
    }


class Transcriber:
    # Set once (per process) when faster-whisper's ONNX VAD fails, so we don't
    # keep paying its cost. Can also be forced off up-front via the
    # KLIPZY_DISABLE_VAD env var for machines where the native VAD hard-crashes.
    _vad_disabled = os.environ.get("KLIPZY_DISABLE_VAD", "").strip().lower() in ("1", "true", "yes")

    def __init__(self, model_size: str = "base", device: Optional[str] = None):
        self.model_size = model_size
        self.device = device
        self._model = None
        self._backend = None
        self._skip_backends = set()  # backends that failed this run; don't retry

    def _detect_device(self) -> str:
        """Auto-detect best available compute device (for openai-whisper fallback)."""
        try:
            import torch
            if torch.cuda.is_available():
                return "cuda"
            if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                return "mps"
        except ImportError:
            pass
        return "cpu"

    def _faster_device(self):
        """
        Pick a SAFE device + compute type for faster-whisper (CTranslate2).

        CTranslate2 does its own CUDA detection independent of PyTorch, and with
        device="auto" it will happily select a CUDA GPU and then crash if the
        CUDA runtime DLLs (e.g. cublas64_12.dll) aren't present — which is the
        case on a machine with an NVIDIA card but CPU-only PyTorch. So only use
        CUDA when PyTorch confirms a working CUDA build; otherwise CPU int8
        (fast and dependency-free). CTNranslate2 has no Metal backend, so Apple
        Silicon uses CPU here (MLX is the preferred M-series path anyway).
        """
        try:
            import torch
            if torch.cuda.is_available():
                return "cuda", "float16"
        except ImportError:
            pass
        return "cpu", "int8"

    def _load_model(self):
        if self._model is not None:
            return

        # 0. A configured remote endpoint wins: nothing to load locally, we just
        #    POST the audio. "remote" is skippable like any other backend, so a
        #    failure at transcribe-time falls through to the local stack below.
        if "remote" not in self._skip_backends:
            try:
                from server.core import asr_client
                if asr_client.is_remote():
                    print("Using the configured remote transcription endpoint...")
                    self._model = "remote"
                    self._backend = "remote"
                    return
            except Exception:
                pass

        # 1. MLX-Whisper: preferred on Apple Silicon (M-series macOS)
        # Native MLX acceleration, supports word_timestamps=True
        if _is_apple_silicon() and "mlx" not in self._skip_backends:
            try:
                import mlx_whisper
                print(f"Loading MLX-Whisper '{self.model_size}' (Apple Silicon MLX acceleration)...")
                # mlx-whisper uses HuggingFace model IDs; map common sizes
                # If model_size looks like a path or HF repo (contains / or \\), pass through directly
                if "/" in self.model_size or "\\" in self.model_size:
                    mlx_model_id = self.model_size
                else:
                    # Use the "-mlx" suffixed repos: mlx-community publishes
                    # every size under that name, whereas the bare names are
                    # inconsistent (e.g. whisper-base / whisper-large-v3 return
                    # 401/Not Found), which crashed the MLX backend and forced a
                    # fallback to CPU faster-whisper.
                    mlx_model_map = {
                        "tiny": "mlx-community/whisper-tiny-mlx",
                        "base": "mlx-community/whisper-base-mlx",
                        "small": "mlx-community/whisper-small-mlx",
                        "medium": "mlx-community/whisper-medium-mlx",
                        "large": "mlx-community/whisper-large-v3-mlx",
                        "large-v2": "mlx-community/whisper-large-v2-mlx",
                        "large-v3": "mlx-community/whisper-large-v3-mlx",
                    }
                    mlx_model_id = mlx_model_map.get(self.model_size, f"mlx-community/whisper-{self.model_size}-mlx")
                self._model = mlx_model_id  # store model ID string for mlx_whisper.transcribe()
                self._backend = "mlx"
                return
            except ImportError:
                pass  # fall through to faster-whisper

        # 2. Faster-Whisper: CTranslate2 backend, fast on CPU/GPU
        if "faster" not in self._skip_backends:
            try:
                from faster_whisper import WhisperModel
                fw_device, fw_compute = self._faster_device()
                print(f"Loading Faster-Whisper '{self.model_size}' on {fw_device} ({fw_compute})...")
                self._model = WhisperModel(self.model_size, device=fw_device, compute_type=fw_compute)
                self._backend = "faster"
                return
            except ImportError:
                pass

        # 3. OpenAI Whisper (PyTorch): fallback with CUDA/MPS/CPU
        try:
            import whisper
        except ImportError:
            raise ImportError(
                "No transcription backend available. Install one of:\n"
                "  pip install mlx-whisper          (Apple Silicon, recommended)\n"
                "  pip install faster-whisper       (cross-platform, 3-5x faster)\n"
                "  pip install openai-whisper       (fallback)"
            )

        if self.device is None:
            self.device = self._detect_device()

        print(f"Loading Whisper model '{self.model_size}' on {self.device}...")
        self._model = whisper.load_model(self.model_size, device=self.device)
        self._backend = "openai"

    def transcribe(self, audio_path: str, language: Optional[str] = None) -> List[TranscriptSegment]:
        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        self._load_model()
        try:
            if self._backend == "remote":
                return self._transcribe_remote(audio_path, language)
            if self._backend == "mlx":
                return self._transcribe_mlx(audio_path, language)
            if self._backend == "faster":
                return self._transcribe_faster(audio_path, language)
            return self._transcribe_openai(audio_path, language)
        except Exception as e:
            # Any non-final backend can fail at runtime — a remote server that's
            # gone away, a model download error, or a CUDA runtime that isn't
            # actually usable. Mark it skipped and fall back to the next backend
            # instead of crashing the whole job. openai-whisper is the final,
            # dependency-light backstop, so a failure there is genuinely fatal.
            if self._backend in ("remote", "mlx", "faster"):
                print(f"{self._backend}-whisper failed ({e}); falling back to the next backend...")
                self._skip_backends.add(self._backend)
                self._backend = None
                self._model = None
                self._load_model()
                return self.transcribe(audio_path, language)
            raise

    def _transcribe_remote(self, audio_path: str, language: Optional[str] = None):
        """Transcribe via the configured OpenAI-compatible endpoint (whisper.cpp
        whisper-server, faster-whisper-server, Speaches, OpenAI...)."""
        from server.core.asr_client import transcribe_remote

        raw_segments, real_words = transcribe_remote(audio_path, language=language)
        if not real_words:
            # Be explicit: the karaoke highlight will be evenly spaced rather
            # than truly aligned, because the server didn't return word times.
            print("Remote endpoint returned no word timestamps — caption timing "
                  "will be approximated by spreading words across each segment.")
        segments: List[TranscriptSegment] = []
        for s in raw_segments:
            words = [
                WordTimestamp(word=w["word"], start=w["start"], end=w["end"])
                for w in s.get("words", [])
            ]
            segments.append(TranscriptSegment(
                id=s.get("id", 0), start=s.get("start", 0.0),
                end=s.get("end", 0.0), text=s.get("text", ""), words=words,
            ))
        return segments

    def _transcribe_mlx(self, audio_path: str, language: Optional[str] = None):
        """MLX-Whisper transcription on Apple Silicon."""
        import mlx_whisper

        options = {"word_timestamps": True}
        if language:
            options["language"] = language

        result = mlx_whisper.transcribe(audio_path, path_or_hf_repo=self._model, **options)

        segments: List[TranscriptSegment] = []
        for s in result.get("segments", []):
            words = [
                WordTimestamp(
                    word=w.get("word", "").strip(),
                    start=round(float(w.get("start", 0.0)), 3),
                    end=round(float(w.get("end", 0.0)), 3),
                    probability=round(float(w.get("probability", 1.0)), 3),
                )
                for w in s.get("words", [])
            ]
            segments.append(
                TranscriptSegment(
                    id=s.get("id", 0),
                    start=round(float(s.get("start", 0.0)), 3),
                    end=round(float(s.get("end", 0.0)), 3),
                    text=s.get("text", "").strip(),
                    words=words,
                )
            )
        return segments

    def _transcribe_faster(self, audio_path: str, language: Optional[str] = None):
        # vad_filter uses an ONNX Silero VAD via onnxruntime. On some Windows
        # setups that native path hard-crashes the whole process (not a catchable
        # Python exception), so a plain try/except can't save us. Guard it: try
        # with VAD once, and if this machine is known-bad, skip straight to
        # no-VAD. A crash still can't be trapped, but retry-without-VAD covers the
        # cases where it raises instead of aborting, and the class-level flag
        # avoids paying the crash cost twice in one run.
        def _run(use_vad: bool):
            return self._model.transcribe(
                audio_path,
                language=language,
                word_timestamps=True,
                vad_filter=use_vad,
            )

        if getattr(Transcriber, "_vad_disabled", False):
            segments, _info = _run(False)
        else:
            try:
                segments, _info = _run(True)
            except Exception as e:
                # VAD raised (bad onnxruntime, missing model, etc.) — remember it
                # and fall back to a no-VAD pass so transcription still succeeds.
                print(f"faster-whisper VAD failed ({e}); retrying without VAD...")
                Transcriber._vad_disabled = True
                segments, _info = _run(False)
        out: List[TranscriptSegment] = []
        for s in segments:
            words = [
                WordTimestamp(
                    word=(w.word or "").strip(),
                    start=round(float(w.start), 3),
                    end=round(float(w.end), 3),
                    probability=round(float(w.probability), 3),
                )
                for w in (s.words or [])
            ]
            out.append(
                TranscriptSegment(
                    id=len(out),
                    start=round(float(s.start), 3),
                    end=round(float(s.end), 3),
                    text=(s.text or "").strip(),
                    words=words,
                )
            )
        return out

    def _transcribe_openai(self, audio_path: str, language: Optional[str]):
        options = {"word_timestamps": True}
        if language:
            options["language"] = language

        import warnings
        with warnings.catch_warnings():
            # Triton DTW / median kernel warnings are expected on Windows where
            # the full CUDA C/C++ compilation toolkit is not present.
            warnings.filterwarnings(
                "ignore",
                message="Failed to launch Triton kernels.*",
                category=UserWarning,
            )
            result = self._model.transcribe(audio_path, **options)

        segments: List[TranscriptSegment] = []
        for s in result.get("segments", []):
            words = [
                WordTimestamp(
                    word=w.get("word", "").strip(),
                    start=round(w.get("start", 0.0), 3),
                    end=round(w.get("end", 0.0), 3),
                    probability=round(w.get("probability", 1.0), 3),
                )
                for w in s.get("words", [])
            ]
            segments.append(
                TranscriptSegment(
                    id=s.get("id", 0),
                    start=round(s.get("start", 0.0), 3),
                    end=round(s.get("end", 0.0), 3),
                    text=s.get("text", "").strip(),
                    words=words,
                )
            )
        return segments
def split_transcript_gaps(segments, max_gap=3.0):
    """Repair ASR segments that bridge long silence using real word timestamps.

    Never interpolate timestamps or move speech to unrelated action windows.
    Segments without word timing retain their original text and bounds.
    """
    repaired=[]
    for seg in segments:
        words=seg.words or []
        groups=[]
        for word in words:
            if not groups or word.start-groups[-1][-1].end > max_gap:
                groups.append([])
            groups[-1].append(word)
        if len(groups)<=1:
            repaired.append(seg.model_copy(update={'id':len(repaired)}))
            continue
        for group in groups:
            repaired.append(TranscriptSegment(id=len(repaired),start=group[0].start,end=group[-1].end,
                text=' '.join(w.word.strip() for w in group).strip(),words=group,speaker=seg.speaker))
    return repaired
