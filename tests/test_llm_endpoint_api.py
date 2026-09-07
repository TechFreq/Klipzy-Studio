"""End-to-end checks for the AI-engine settings endpoints the Setup panel calls."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("KLIPZY_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("KLIPZY_DISABLE_AUTH", "1")
    from server.api.server import app
    return TestClient(app)


class _Handler(BaseHTTPRequestHandler):
    def _json(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self._json(200, {"data": []})

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0)
        self._json(200, {"choices": [{"message": {"content": "ready"}}]})

    def log_message(self, *_a):
        pass


@pytest.fixture
def stub():
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_defaults_to_builtin_ollama(client):
    r = client.get("/api/setup/llm-endpoint")
    assert r.status_code == 200
    assert r.json()["backend"] == "ollama"


def test_save_custom_endpoint_and_read_back(client, stub):
    r = client.post("/api/setup/llm-endpoint", json={
        "backend": "openai", "base_url": stub, "model": "stub-model", "api_key": "sk-abc",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["backend"] == "openai"
    assert body["model"] == "stub-model"
    assert body["api_key_set"] is True
    assert "sk-abc" not in json.dumps(body)      # key never echoed back

    again = client.get("/api/setup/llm-endpoint").json()
    assert again["base_url"] == f"{stub}/v1"     # normalized for the user


def test_custom_endpoint_requires_address(client):
    r = client.post("/api/setup/llm-endpoint", json={"backend": "openai", "base_url": ""})
    assert r.status_code == 400


def test_test_endpoint_reports_success_without_saving(client, stub):
    r = client.post("/api/setup/llm-test", json={
        "backend": "openai", "base_url": stub, "model": "stub-model",
    })
    assert r.status_code == 200 and r.json()["ok"] is True
    assert "ready" in r.json()["reply"]
    # Testing must NOT persist the settings.
    assert client.get("/api/setup/llm-endpoint").json()["backend"] == "ollama"


def test_test_endpoint_reports_failure_gracefully(client):
    r = client.post("/api/setup/llm-test", json={
        "backend": "openai", "base_url": "http://127.0.0.1:1", "model": "m",
    })
    assert r.status_code == 200          # a failed probe is a result, not a crash
    assert r.json()["ok"] is False
    assert r.json()["error"]


def test_switching_back_to_ollama(client, stub):
    client.post("/api/setup/llm-endpoint", json={
        "backend": "openai", "base_url": stub, "model": "m"})
    r = client.post("/api/setup/llm-endpoint", json={"backend": "ollama"})
    assert r.status_code == 200 and r.json()["backend"] == "ollama"
