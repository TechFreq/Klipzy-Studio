"""Tests for optional remote speech-to-text (whisper.cpp whisper-server and any
other OpenAI audio-transcription compatible server).

The parser gets the most attention: servers disagree about where word timings
live, and Klipzy's karaoke captions depend on getting them right.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from server.core import asr_client


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path, monkeypatch):
    monkeypatch.setenv("KLIPZY_LOG_DIR", str(tmp_path))
    yield


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("http://localhost:8080/v1", "http://localhost:8080/v1"),
    ("localhost:8080", "http://localhost:8080/v1"),
    ("http://localhost:8080", "http://localhost:8080/v1"),
    ("http://localhost:8080/v1/audio/transcriptions", "http://localhost:8080/v1"),
    ("http://localhost:8080/inference", "http://localhost:8080/v1"),
    ("", ""),
])
def test_normalize_base_url(raw, expected):
    assert asr_client.normalize_base_url(raw) == expected


def test_defaults_to_local():
    assert asr_client.load_config()["backend"] == asr_client.BACKEND_LOCAL
    assert asr_client.is_remote() is False


def test_remote_requires_address():
    with pytest.raises(ValueError):
        asr_client.save_config("openai", "")


def test_remote_without_address_falls_back_to_local(tmp_path):
    (tmp_path / "asr_endpoint.json").write_text(
        json.dumps({"backend": "openai", "base_url": ""}), encoding="utf-8")
    assert asr_client.load_config()["backend"] == asr_client.BACKEND_LOCAL


def test_corrupt_config_survivable(tmp_path):
    (tmp_path / "asr_endpoint.json").write_text("}{ nope", encoding="utf-8")
    assert asr_client.load_config()["backend"] == asr_client.BACKEND_LOCAL


def test_describe_hides_api_key():
    asr_client.save_config("openai", "localhost:9", api_key="sk-secret", model="whisper-1")
    d = asr_client.describe()
    assert d["api_key_set"] is True
    assert "sk-secret" not in json.dumps(d)


# ---------------------------------------------------------------------------
# Parsing the three real-world response shapes
# ---------------------------------------------------------------------------
def test_parse_segment_level_word_timings():
    """Best case: words nested in each segment -> real alignment."""
    segs, real = asr_client.parse_verbose_json({
        "segments": [
            {"id": 0, "start": 0.0, "end": 1.0, "text": "hello there", "words": [
                {"word": "hello", "start": 0.0, "end": 0.4},
                {"word": "there", "start": 0.5, "end": 1.0},
            ]},
        ]
    })
    assert real is True
    assert len(segs) == 1
    assert [w["word"] for w in segs[0]["words"]] == ["hello", "there"]
    assert segs[0]["words"][1]["start"] == 0.5


def test_parse_top_level_words_are_split_into_segments():
    """Some servers return one flat words[] array; assign by time overlap."""
    segs, real = asr_client.parse_verbose_json({
        "segments": [
            {"id": 0, "start": 0.0, "end": 1.0, "text": "one two"},
            {"id": 1, "start": 1.0, "end": 2.0, "text": "three"},
        ],
        "words": [
            {"word": "one", "start": 0.0, "end": 0.4},
            {"word": "two", "start": 0.5, "end": 0.9},
            {"word": "three", "start": 1.1, "end": 1.8},
        ],
    })
    assert real is True
    assert [w["word"] for w in segs[0]["words"]] == ["one", "two"]
    assert [w["word"] for w in segs[1]["words"]] == ["three"]


def test_parse_segments_only_spreads_words_and_flags_approximation():
    """whisper.cpp without word granularity: even spacing, and the caller MUST
    be told it's approximate so the UI doesn't imply real alignment."""
    segs, real = asr_client.parse_verbose_json({
        "segments": [{"id": 0, "start": 0.0, "end": 4.0, "text": "a b c d"}]
    })
    assert real is False                       # the honesty flag
    words = segs[0]["words"]
    assert [w["word"] for w in words] == ["a", "b", "c", "d"]
    # Evenly spaced across the 4s span, in order, inside the segment.
    assert words[0]["start"] == 0.0
    assert words[1]["start"] == 1.0
    assert words[3]["end"] == pytest.approx(4.0, abs=0.01)
    assert all(w["end"] >= w["start"] for w in words)


def test_parse_text_only_response():
    """A bare {"text": ...} still yields one usable segment."""
    segs, real = asr_client.parse_verbose_json({"text": "just words", "duration": 2.0})
    assert real is False
    assert len(segs) == 1 and segs[0]["text"] == "just words"
    assert segs[0]["words"]


@pytest.mark.parametrize("payload", [{}, {"segments": []}, {"segments": "bad"},
                                     {"text": ""}, {"segments": [None]}])
def test_parse_handles_junk(payload):
    segs, real = asr_client.parse_verbose_json(payload)
    assert segs == [] and real is False


def test_parse_skips_words_with_unusable_timings():
    segs, _ = asr_client.parse_verbose_json({
        "segments": [{"id": 0, "start": 0.0, "end": 1.0, "text": "ok", "words": [
            {"word": "good", "start": 0.0, "end": 0.5},
            {"word": "bad", "start": None, "end": "x"},
            {"word": "", "start": 0.6, "end": 0.9},
        ]}]
    })
    assert [w["word"] for w in segs[0]["words"]] == ["good"]


# ---------------------------------------------------------------------------
# Real HTTP round-trip against a stub whisper-server
# ---------------------------------------------------------------------------
class _Stub(BaseHTTPRequestHandler):
    received = {}

    def _json(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self._json(200, {"data": [{"id": "whisper-1"}]})

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0)
        type(self).received = {
            "ctype": self.headers.get("Content-Type", ""),
            "auth": self.headers.get("Authorization"),
            "size": len(body),
            "body": body,
        }
        self._json(200, {"text": "hi", "duration": 1.0, "segments": [
            {"id": 0, "start": 0.0, "end": 1.0, "text": "hi", "words": [
                {"word": "hi", "start": 0.0, "end": 0.5}]}]})

    def log_message(self, *_a):
        pass


@pytest.fixture
def stub():
    srv = HTTPServer(("127.0.0.1", 0), _Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_remote_transcribe_round_trip(stub, tmp_path):
    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"RIFF----WAVEfmt " + b"\0" * 32)   # bytes are enough for the stub
    asr_client.save_config("openai", stub, api_key="sk-t", model="whisper-1")

    segs, real = asr_client.transcribe_remote(str(audio))
    assert real is True
    assert segs[0]["text"] == "hi"

    sent = _Stub.received
    assert sent["ctype"].startswith("multipart/form-data; boundary=")
    assert sent["auth"] == "Bearer sk-t"
    # The request must carry the file part and ask for verbose_json + word times.
    assert b'name="file"; filename="clip.wav"' in sent["body"]
    assert b"verbose_json" in sent["body"]
    assert b"timestamp_granularities[]" in sent["body"]
    assert b"whisper-1" in sent["body"]


def test_remote_is_available(stub):
    asr_client.save_config("openai", stub, model="whisper-1")
    assert asr_client.is_available() is True


def test_remote_unreachable_raises_so_caller_falls_back(tmp_path):
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"\0" * 16)
    asr_client.save_config("openai", "http://127.0.0.1:1", model="m")
    assert asr_client.is_available() is False
    with pytest.raises(RuntimeError):
        asr_client.transcribe_remote(str(audio))


def test_transcriber_reports_remote_as_active(stub):
    """detect_active_backend drives the header readout, so it must show the
    remote endpoint rather than the local library that isn't being used."""
    from server.core.transcriber import detect_active_backend
    asr_client.save_config("openai", stub, model="whisper-1")
    info = detect_active_backend()
    assert info["active"] == "remote"
    assert "remote" in info["available"]
    assert stub in info["note"]
