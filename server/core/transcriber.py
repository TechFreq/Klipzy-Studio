"""
Transcription module using OpenAI Whisper / Faster-Whisper.
Cross-platform: auto-detects CUDA (Windows/Linux) or MPS (macOS Apple Silicon).
"""

import os
from typing import List, Optional

from server.models import TranscriptSegment, WordTimestamp


class Transcriber:
    def __init__(self, model_size: str = "base", device: Optional[str] = None):
        self.model_size = model_size
        self.device = device
        self._model = None

    def _detect_device(self) -> str:
        """Auto-detect best available compute device."""
        try:
            import torch
            if torch.cuda.is_available():
                return "cuda"
            if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                return "mps"
        except ImportError:
            pass
        return "cpu"

    def _load_model(self):
        if self._model is not None:
            return
        try:
            import whisper
        except ImportError:
            raise ImportError("openai-whisper is not installed. Run: pip install openai-whisper")

        if self.device is None:
            self.device = self._detect_device()

        print(f"Loading Whisper model '{self.model_size}' on {self.device}...")
        self._model = whisper.load_model(self.model_size, device=self.device)

    def transcribe(self, audio_path: str, language: Optional[str] = None) -> List[TranscriptSegment]:
        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        self._load_model()

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