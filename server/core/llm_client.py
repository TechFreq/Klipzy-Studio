"""
Single seam for every local/remote LLM call in the app.

Klipzy ships with Ollama as the built-in default, but Ollama, llama.cpp's
``llama-server`` and LM Studio all expose the SAME OpenAI-compatible
``/v1/chat/completions`` contract — so supporting "anything else" is one
configurable endpoint, not a second edition of the app.

Two backends:
  * ``ollama``  - the bundled default, via the ``ollama`` Python package.
  * ``openai``  - any OpenAI-compatible base URL (llama-server, LM Studio,
                  vLLM, a cloud endpoint, or even Ollama's own /v1 route).

Every caller goes through :func:`chat`, which returns plain text (or a JSON
string when ``json_mode=True``) and raises on failure so the existing
try/except fallbacks in llm_detector / hook_writer / edit_chat / translator
keep degrading to their heuristic paths exactly as before.

Deliberately dependency-free on the HTTP side: it uses ``urllib`` from the
stdlib rather than adding ``requests``/``openai`` to a local-first app.
"""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

BACKEND_OLLAMA = "ollama"
BACKEND_OPENAI = "openai"
VALID_BACKENDS = (BACKEND_OLLAMA, BACKEND_OPENAI)

# Ollama's native API port, used for the built-in backend's health probe.
OLLAMA_PORT = 11434

DEFAULT_TIMEOUT = 120.0


# ----------------------------------------------------------------------
# Configuration (persisted next to the other prefs in the gitignored logs dir)
# ----------------------------------------------------------------------
def _config_path() -> Path:
    base = Path(os.environ["KLIPZY_LOG_DIR"]) if os.environ.get("KLIPZY_LOG_DIR") \
        else (Path(__file__).resolve().parents[2] / "logs")
    return base / "llm_endpoint.json"


def normalize_base_url(url: str) -> str:
    """Turn whatever the user pasted into a usable API root.

    People paste all of these, and every one of them should work:
      ``localhost:1234``, ``http://localhost:1234``, ``http://localhost:1234/v1``,
      ``http://localhost:1234/v1/chat/completions``
    Returns a bare root ending in ``/v1`` (no trailing slash), or "" if blank.
    """
    u = (url or "").strip().rstrip("/")
    if not u:
        return ""
    if "://" not in u:
        u = f"http://{u}"
    # Tolerate a full endpoint path being pasted in.
    for suffix in ("/chat/completions", "/completions"):
        if u.endswith(suffix):
            u = u[: -len(suffix)].rstrip("/")
    if not u.endswith("/v1"):
        u = f"{u}/v1"
    return u


def load_config() -> Dict[str, Any]:
    """Current LLM backend settings. Always returns a usable dict."""
    cfg = {"backend": BACKEND_OLLAMA, "base_url": "", "api_key": "", "model": ""}
    try:
        p = _config_path()
        if p.is_file():
            saved = json.loads(p.read_text(encoding="utf-8") or "{}")
            if isinstance(saved, dict):
                cfg.update({k: saved.get(k, cfg[k]) for k in cfg})
    except Exception:
        pass  # unreadable/corrupt config must never break AI features
    if cfg.get("backend") not in VALID_BACKENDS:
        cfg["backend"] = BACKEND_OLLAMA
    cfg["base_url"] = normalize_base_url(cfg.get("base_url", ""))
    # A custom endpoint with no URL configured is meaningless — fall back so the
    # app keeps working instead of failing every AI call.
    if cfg["backend"] == BACKEND_OPENAI and not cfg["base_url"]:
        cfg["backend"] = BACKEND_OLLAMA
    return cfg


def save_config(backend: str, base_url: str = "", api_key: str = "", model: str = "") -> Dict[str, Any]:
    """Persist the backend settings and return the stored config."""
    backend = (backend or BACKEND_OLLAMA).strip().lower()
    if backend not in VALID_BACKENDS:
        raise ValueError(f"Unknown LLM backend: {backend}")
    cfg = {
        "backend": backend,
        "base_url": normalize_base_url(base_url),
        "api_key": (api_key or "").strip(),
        "model": (model or "").strip(),
    }
    if backend == BACKEND_OPENAI and not cfg["base_url"]:
        raise ValueError("A server address is required for a custom OpenAI-compatible endpoint")
    p = _config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return cfg


def describe() -> Dict[str, Any]:
    """Backend summary for the UI. Never leaks the API key, only whether it's set."""
    cfg = load_config()
    return {
        "backend": cfg["backend"],
        "base_url": cfg["base_url"],
        "model": cfg["model"],
        "api_key_set": bool(cfg["api_key"]),
        "available": is_available(cfg),
    }


# ----------------------------------------------------------------------
# Availability
# ----------------------------------------------------------------------
def _port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def is_available(cfg: Optional[Dict[str, Any]] = None) -> bool:
    """Is the configured backend actually reachable right now?

    Replaces the old ``detect_ollama()['running']`` port probe at the call
    gates, which was hardcoded to localhost:11434 and so always reported a
    remote endpoint as "not running".
    """
    cfg = cfg or load_config()
    if cfg["backend"] == BACKEND_OPENAI:
        try:
            req = urllib.request.Request(
                f"{cfg['base_url']}/models", headers=_auth_headers(cfg), method="GET"
            )
            with urllib.request.urlopen(req, timeout=4.0) as resp:
                return 200 <= resp.status < 500  # 401/404 still means "served"
        except urllib.error.HTTPError:
            # It answered, so something is listening and speaking HTTP.
            return True
        except Exception:
            return False
    return _port_open(OLLAMA_PORT)


def _auth_headers(cfg: Dict[str, Any]) -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if cfg.get("api_key"):
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    return headers


# ----------------------------------------------------------------------
# Chat
# ----------------------------------------------------------------------
def extract_message_text(data: Dict[str, Any]) -> str:
    """Pull the assistant text out of an OpenAI-compatible response.

    Reasoning models (llama.cpp, DeepSeek-R1 and friends) put their visible
    answer in ``content`` but sometimes leave it empty and only fill
    ``reasoning_content``; prefer the real answer and fall back rather than
    returning "" and tripping the caller's fallback path for no reason.
    """
    try:
        message = (data.get("choices") or [{}])[0].get("message") or {}
    except (AttributeError, IndexError, TypeError):
        return ""
    content = (message.get("content") or "").strip()
    if content:
        return content
    return (message.get("reasoning_content") or "").strip()


def chat(
    messages: List[Dict[str, str]],
    json_mode: bool = False,
    model: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> str:
    """Send a chat completion and return the assistant's text.

    ``model`` is the Ollama model name the caller already resolved; for a custom
    endpoint the configured model wins (remote servers name models differently),
    falling back to the caller's value when none is configured.

    Raises on any transport/parse failure so existing callers keep their
    heuristic fallbacks.
    """
    cfg = load_config()
    if cfg["backend"] == BACKEND_OPENAI:
        return _chat_openai(cfg, messages, json_mode, model, timeout)
    return _chat_ollama(messages, json_mode, model)


def _chat_ollama(messages: List[Dict[str, str]], json_mode: bool, model: Optional[str]) -> str:
    import ollama  # lazy: keeps the package optional

    kwargs: Dict[str, Any] = {"model": model, "messages": messages}
    if json_mode:
        kwargs["format"] = "json"
    resp = ollama.chat(**kwargs)
    return ((resp.get("message", {}) or {}).get("content", "") or "").strip()


def _chat_openai(
    cfg: Dict[str, Any],
    messages: List[Dict[str, str]],
    json_mode: bool,
    model: Optional[str],
    timeout: float,
) -> str:
    payload: Dict[str, Any] = {
        "model": cfg.get("model") or model or "local-model",
        "messages": messages,
        "stream": False,
    }
    if json_mode:
        # Widely supported by llama-server / LM Studio / vLLM / OpenAI. Servers
        # that don't know the field generally ignore it; the caller's json.loads
        # failure then falls back to the heuristic, same as a weak local model.
        payload["response_format"] = {"type": "json_object"}

    req = urllib.request.Request(
        f"{cfg['base_url']}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers=_auth_headers(cfg),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            pass
        raise RuntimeError(f"LLM endpoint returned HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Could not reach the LLM endpoint: {e.reason}") from e

    return extract_message_text(json.loads(body))


def unload(model: Optional[str]) -> None:
    """Best-effort VRAM release between heavy stages.

    Only meaningful for the built-in Ollama backend (``keep_alive=0``); a remote
    server manages its own memory, so this is a no-op there.
    """
    if load_config()["backend"] != BACKEND_OLLAMA or not model:
        return
    try:
        import ollama
        ollama.generate(model=model, prompt="", keep_alive=0)
    except Exception:
        pass
