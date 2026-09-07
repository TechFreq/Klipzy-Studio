"""Tests for the configurable LLM backend (built-in Ollama vs any
OpenAI-compatible endpoint). The HTTP layer is exercised against a real
throwaway server so the request/response contract is actually verified."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from server.core import llm_client


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path, monkeypatch):
    """Keep every test off the developer's real logs/llm_endpoint.json."""
    monkeypatch.setenv("KLIPZY_LOG_DIR", str(tmp_path))
    yield


# ---------------------------------------------------------------------------
# URL normalization — users paste all sorts of things
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("http://localhost:1234/v1", "http://localhost:1234/v1"),
    ("http://localhost:1234", "http://localhost:1234/v1"),
    ("localhost:1234", "http://localhost:1234/v1"),
    ("http://localhost:1234/", "http://localhost:1234/v1"),
    ("http://localhost:1234/v1/chat/completions", "http://localhost:1234/v1"),
    ("https://api.example.com/v1", "https://api.example.com/v1"),
    ("", ""),
    ("   ", ""),
])
def test_normalize_base_url(raw, expected):
    assert llm_client.normalize_base_url(raw) == expected


# ---------------------------------------------------------------------------
# Config persistence + safe defaults
# ---------------------------------------------------------------------------
def test_defaults_to_ollama_when_unconfigured():
    cfg = llm_client.load_config()
    assert cfg["backend"] == llm_client.BACKEND_OLLAMA


def test_save_and_load_roundtrip():
    llm_client.save_config("openai", "localhost:1234", api_key="sk-x", model="my-model")
    cfg = llm_client.load_config()
    assert cfg["backend"] == "openai"
    assert cfg["base_url"] == "http://localhost:1234/v1"
    assert cfg["model"] == "my-model"


def test_custom_backend_requires_a_url():
    with pytest.raises(ValueError):
        llm_client.save_config("openai", "")


def test_unknown_backend_rejected():
    with pytest.raises(ValueError):
        llm_client.save_config("definitely-not-a-backend", "localhost:1")


def test_custom_backend_without_url_falls_back_to_ollama(tmp_path):
    """A hand-edited/corrupt config must not break every AI feature."""
    (tmp_path / "llm_endpoint.json").write_text(
        json.dumps({"backend": "openai", "base_url": ""}), encoding="utf-8")
    assert llm_client.load_config()["backend"] == llm_client.BACKEND_OLLAMA


def test_corrupt_config_is_survivable(tmp_path):
    (tmp_path / "llm_endpoint.json").write_text("{not json at all", encoding="utf-8")
    assert llm_client.load_config()["backend"] == llm_client.BACKEND_OLLAMA


def test_describe_never_leaks_the_api_key():
    llm_client.save_config("openai", "localhost:9", api_key="sk-secret", model="m")
    described = llm_client.describe()
    assert described["api_key_set"] is True
    assert "sk-secret" not in json.dumps(described)


# ---------------------------------------------------------------------------
# Response parsing, including reasoning models
# ---------------------------------------------------------------------------
def test_extract_message_text_standard():
    assert llm_client.extract_message_text(
        {"choices": [{"message": {"content": "hello"}}]}) == "hello"


def test_extract_message_text_falls_back_to_reasoning_content():
    """llama.cpp / DeepSeek-R1 style: empty content, answer in reasoning_content."""
    assert llm_client.extract_message_text(
        {"choices": [{"message": {"content": "", "reasoning_content": "thought"}}]}) == "thought"


def test_extract_message_text_prefers_content_over_reasoning():
    assert llm_client.extract_message_text(
        {"choices": [{"message": {"content": "answer", "reasoning_content": "noise"}}]}) == "answer"


@pytest.mark.parametrize("payload", [{}, {"choices": []}, {"choices": [{}]}, {"choices": "bad"}])
def test_extract_message_text_handles_junk(payload):
    assert llm_client.extract_message_text(payload) == ""


# ---------------------------------------------------------------------------
# Real HTTP round-trip against a stub OpenAI-compatible server
# ---------------------------------------------------------------------------
class _StubHandler(BaseHTTPRequestHandler):
    received = {}

    def _json(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path.endswith("/models"):
            self._json(200, {"data": [{"id": "stub-model"}]})
        else:
            self._json(404, {"error": "nope"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        type(self).received = {"body": body, "auth": self.headers.get("Authorization")}
        self._json(200, {"choices": [{"message": {"content": "pong"}}]})

    def log_message(self, *_args):
        pass  # keep the test output clean


@pytest.fixture
def stub_server():
    srv = HTTPServer(("127.0.0.1", 0), _StubHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_openai_chat_round_trip(stub_server):
    llm_client.save_config("openai", stub_server, api_key="sk-test", model="stub-model")
    reply = llm_client.chat([{"role": "user", "content": "ping"}], json_mode=True)

    assert reply == "pong"
    sent = _StubHandler.received
    assert sent["body"]["model"] == "stub-model"
    assert sent["body"]["messages"] == [{"role": "user", "content": "ping"}]
    assert sent["body"]["stream"] is False
    # json_mode must map onto the OpenAI-compatible field.
    assert sent["body"]["response_format"] == {"type": "json_object"}
    assert sent["auth"] == "Bearer sk-test"


def test_openai_is_available_against_live_server(stub_server):
    llm_client.save_config("openai", stub_server, model="stub-model")
    assert llm_client.is_available() is True


def test_openai_is_available_false_when_nothing_listening():
    # Port 1 is reserved and never serving.
    llm_client.save_config("openai", "http://127.0.0.1:1", model="m")
    assert llm_client.is_available() is False


def test_openai_chat_raises_so_callers_can_fall_back():
    """Callers rely on an exception to trigger their heuristic fallback."""
    llm_client.save_config("openai", "http://127.0.0.1:1", model="m")
    with pytest.raises(RuntimeError):
        llm_client.chat([{"role": "user", "content": "hi"}])


def test_unload_is_a_noop_for_remote_backends(stub_server):
    """Remote servers manage their own memory; keep_alive=0 is Ollama-only."""
    llm_client.save_config("openai", stub_server, model="m")
    llm_client.unload("m")  # must not raise, must not call the endpoint
    assert _StubHandler.received.get("body", {}).get("prompt") is None
