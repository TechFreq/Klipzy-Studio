"""
Optional remote speech-to-text, for subtitle generation.

Klipzy transcribes locally by default (mlx-whisper / faster-whisper /
openai-whisper, picked automatically). This module adds ONE optional
alternative: any server that speaks the OpenAI audio-transcription API

    POST {base_url}/audio/transcriptions      (multipart/form-data)

which covers whisper.cpp's ``whisper-server``, faster-whisper-server, Speaches,
and OpenAI itself — the same "one contract, many servers" trick the LLM engine
setting uses (see llm_client.py).

Klipzy's karaoke captions need WORD-level timings, so ``verbose_json`` is
requested with word granularity. Servers that only return segments still work:
their words are spread evenly across each segment's span, which animates
sensibly but is an approximation, not real per-word alignment. That distinction
is reported back to the caller so the UI can be honest about it.

Stdlib only (urllib + a hand-rolled multipart body) to avoid adding a
dependency to a local-first app.
"""

from __future__ import annotations

import json
import mimetypes
import os
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BACKEND_LOCAL = "local"
BACKEND_OPENAI = "openai"
VALID_BACKENDS = (BACKEND_LOCAL, BACKEND_OPENAI)

DEFAULT_TIMEOUT = 900.0          # transcription is slow; be patient
DEFAULT_MODEL = "whisper-1"      # what most OpenAI-compatible servers accept


# ----------------------------------------------------------------------
# Configuration (same shape/location convention as the LLM engine settings)
# ----------------------------------------------------------------------
def _config_path() -> Path:
    base = Path(os.environ["KLIPZY_LOG_DIR"]) if os.environ.get("KLIPZY_LOG_DIR") \
        else (Path(__file__).resolve().parents[2] / "logs")
    return base / "asr_endpoint.json"


def normalize_base_url(url: str) -> str:
    """Accept whatever the user pastes and return an API root ending in /v1.

    Handles ``localhost:8080``, ``http://localhost:8080``, ``.../v1`` and a
    full ``.../v1/audio/transcriptions`` endpoint path.
    """
    u = (url or "").strip().rstrip("/")
    if not u:
        return ""
    if "://" not in u:
        u = f"http://{u}"
    for suffix in ("/audio/transcriptions", "/inference"):
        if u.endswith(suffix):
            u = u[: -len(suffix)].rstrip("/")
    if not u.endswith("/v1"):
        u = f"{u}/v1"
    return u


def load_config() -> Dict[str, Any]:
    cfg = {"backend": BACKEND_LOCAL, "base_url": "", "api_key": "", "model": ""}
    try:
        p = _config_path()
        if p.is_file():
            saved = json.loads(p.read_text(encoding="utf-8") or "{}")
            if isinstance(saved, dict):
                cfg.update({k: saved.get(k, cfg[k]) for k in cfg})
    except Exception:
        pass  # a corrupt config must never block transcription
    if cfg.get("backend") not in VALID_BACKENDS:
        cfg["backend"] = BACKEND_LOCAL
    cfg["base_url"] = normalize_base_url(cfg.get("base_url", ""))
    # Remote selected but no address = meaningless; fall back to local so a
    # clipping run never dies on a half-finished setting.
    if cfg["backend"] == BACKEND_OPENAI and not cfg["base_url"]:
        cfg["backend"] = BACKEND_LOCAL
    return cfg


def save_config(backend: str, base_url: str = "", api_key: str = "", model: str = "") -> Dict[str, Any]:
    backend = (backend or BACKEND_LOCAL).strip().lower()
    if backend not in VALID_BACKENDS:
        raise ValueError(f"Unknown transcription backend: {backend}")
    cfg = {
        "backend": backend,
        "base_url": normalize_base_url(base_url),
        "api_key": (api_key or "").strip(),
        "model": (model or "").strip(),
    }
    if backend == BACKEND_OPENAI and not cfg["base_url"]:
        raise ValueError("A server address is required for a remote transcription endpoint")
    p = _config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return cfg


def is_remote() -> bool:
    return load_config()["backend"] == BACKEND_OPENAI


def describe() -> Dict[str, Any]:
    """Settings summary for the UI. Never returns the API key itself."""
    cfg = load_config()
    return {
        "backend": cfg["backend"],
        "base_url": cfg["base_url"],
        "model": cfg["model"],
        "api_key_set": bool(cfg["api_key"]),
        "available": is_available(cfg) if cfg["backend"] == BACKEND_OPENAI else True,
    }


def _auth_headers(cfg: Dict[str, Any]) -> Dict[str, str]:
    return {"Authorization": f"Bearer {cfg['api_key']}"} if cfg.get("api_key") else {}


def is_available(cfg: Optional[Dict[str, Any]] = None) -> bool:
    """Is the configured remote endpoint answering?"""
    cfg = cfg or load_config()
    if cfg["backend"] != BACKEND_OPENAI:
        return True                     # local backends are handled elsewhere
    try:
        req = urllib.request.Request(f"{cfg['base_url']}/models",
                                     headers=_auth_headers(cfg), method="GET")
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            return 200 <= resp.status < 500
    except urllib.error.HTTPError:
        return True                     # answered => something is serving
    except Exception:
        return False


# ----------------------------------------------------------------------
# Response parsing (pure + unit-tested: this is where the real risk lives)
# ----------------------------------------------------------------------
def parse_verbose_json(data: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], bool]:
    """Normalize an OpenAI-style ``verbose_json`` reply into segment dicts.

    Returns ``(segments, had_real_word_timings)`` where each segment is
    ``{"id", "start", "end", "text", "words": [{"word", "start", "end"}]}``.

    Three shapes are handled, because servers disagree:
      1. ``segments[].words`` present  -> real per-word timings (best).
      2. a top-level ``words[]`` array -> split into segments by time overlap.
      3. segments only                 -> words spread evenly across the span,
         which is an APPROXIMATION; the flag returns False so callers can say so.
    """
    raw_segments = data.get("segments")
    if not isinstance(raw_segments, list) or not raw_segments:
        # Some servers return just {"text": "..."} — one synthetic segment then.
        text = (data.get("text") or "").strip()
        if not text:
            return [], False
        dur = float(data.get("duration") or 0.0)
        raw_segments = [{"id": 0, "start": 0.0, "end": dur, "text": text}]

    top_words = data.get("words")
    top_words = top_words if isinstance(top_words, list) else []

    had_real_words = False
    segments: List[Dict[str, Any]] = []

    for i, seg in enumerate(raw_segments):
        if not isinstance(seg, dict):
            continue
        try:
            s_start = float(seg.get("start") or 0.0)
            s_end = float(seg.get("end") or s_start)
        except (TypeError, ValueError):
            continue
        text = str(seg.get("text") or "").strip()

        words: List[Dict[str, Any]] = []
        seg_words = seg.get("words")
        if isinstance(seg_words, list) and seg_words:
            for w in seg_words:
                if not isinstance(w, dict):
                    continue
                token = str(w.get("word") or w.get("text") or "").strip()
                if not token:
                    continue
                try:
                    w_start = float(w.get("start"))
                    w_end = float(w.get("end"))
                except (TypeError, ValueError):
                    continue
                words.append({"word": token, "start": round(w_start, 3),
                              "end": round(max(w_end, w_start), 3)})
            if words:
                had_real_words = True
        elif top_words:
            # Claim the top-level words that fall inside this segment's span.
            for w in top_words:
                if not isinstance(w, dict):
                    continue
                try:
                    w_start = float(w.get("start"))
                    w_end = float(w.get("end"))
                except (TypeError, ValueError):
                    continue
                if w_start >= s_start - 0.001 and w_start < s_end + 0.001:
                    token = str(w.get("word") or w.get("text") or "").strip()
                    if token:
                        words.append({"word": token, "start": round(w_start, 3),
                                      "end": round(max(w_end, w_start), 3)})
            if words:
                had_real_words = True

        if not words and text:
            # Segment-only server: spread the words evenly. Good enough to
            # animate, but explicitly NOT real alignment.
            tokens = text.split()
            span = max(0.0, s_end - s_start)
            step = (span / len(tokens)) if tokens and span > 0 else 0.0
            for j, tok in enumerate(tokens):
                w_start = s_start + j * step
                words.append({"word": tok, "start": round(w_start, 3),
                              "end": round(w_start + step, 3)})

        segments.append({"id": int(seg.get("id", i) or i), "start": round(s_start, 3),
                         "end": round(s_end, 3), "text": text, "words": words})

    return segments, had_real_words


# ----------------------------------------------------------------------
# Multipart upload
# ----------------------------------------------------------------------
def _encode_multipart(fields: Dict[str, str], file_path: str) -> Tuple[bytes, str]:
    """Build a multipart/form-data body containing the audio file + fields."""
    boundary = f"----klipzy{uuid.uuid4().hex}"
    name = os.path.basename(file_path)
    ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"

    parts: List[bytes] = []
    for key, value in fields.items():
        if value is None:
            continue
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n"
            f"{value}\r\n".encode("utf-8")
        )
    with open(file_path, "rb") as fh:
        payload = fh.read()
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
        f"filename=\"{name}\"\r\nContent-Type: {ctype}\r\n\r\n".encode("utf-8")
    )
    parts.append(payload)
    parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def transcribe_remote(
    audio_path: str,
    language: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Tuple[List[Dict[str, Any]], bool]:
    """Transcribe a file via the configured remote endpoint.

    Returns ``(segments, had_real_word_timings)``. Raises on transport failure so
    the caller can fall back to a local backend.
    """
    cfg = load_config()
    if cfg["backend"] != BACKEND_OPENAI:
        raise RuntimeError("No remote transcription endpoint is configured")
    if not os.path.isfile(audio_path):
        raise FileNotFoundError(audio_path)

    # Probe with a SHORT timeout before committing to the upload. The transcribe
    # timeout has to be generous (minutes, for long audio), but a server that
    # accepts the connection and then never answers would otherwise stall the
    # whole clipping run for that entire window instead of falling back to local
    # Whisper. Failing the cheap check first keeps the fallback fast.
    if not is_available(cfg):
        raise RuntimeError(
            f"Transcription endpoint at {cfg['base_url']} is not responding")

    fields = {
        "model": cfg.get("model") or DEFAULT_MODEL,
        "response_format": "verbose_json",
        # Ask for word granularity; servers that don't support it ignore this
        # and we fall back to even spacing in parse_verbose_json.
        "timestamp_granularities[]": "word",
    }
    if language:
        fields["language"] = language

    body, content_type = _encode_multipart(fields, audio_path)
    headers = {"Content-Type": content_type, **_auth_headers(cfg)}
    req = urllib.request.Request(
        f"{cfg['base_url']}/audio/transcriptions", data=body, headers=headers, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            pass
        raise RuntimeError(f"Transcription endpoint returned HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Could not reach the transcription endpoint: {e.reason}") from e

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Transcription endpoint returned invalid JSON: {raw[:200]}") from e

    segments, real_words = parse_verbose_json(data)
    if not segments:
        raise RuntimeError("Transcription endpoint returned no usable segments")
    return segments, real_words
