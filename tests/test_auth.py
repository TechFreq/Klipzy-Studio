"""
Tests for the local API token guard (server/auth.py).

conftest.py disables enforcement for the rest of the suite, so these tests flip
it back on around each case to check the real behaviour.
"""

import os

import pytest
from fastapi.testclient import TestClient

from server import auth
from server.api.server import API_TOKEN, app

HEADER = auth.TOKEN_HEADER


@pytest.fixture
def enforced():
    """Turn token enforcement on for the duration of a test."""
    previous = os.environ.get(auth.DISABLE_ENV_VAR)
    os.environ[auth.DISABLE_ENV_VAR] = "0"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(auth.DISABLE_ENV_VAR, None)
        else:
            os.environ[auth.DISABLE_ENV_VAR] = previous


def test_health_is_reachable_without_a_token(enforced):
    """The Electron launcher polls /health before it can send anything."""
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200


def test_protected_endpoint_rejects_missing_token(enforced):
    with TestClient(app) as client:
        response = client.get("/caption-presets")
    assert response.status_code == 401
    assert HEADER in response.json()["detail"]


def test_protected_endpoint_rejects_wrong_token(enforced):
    with TestClient(app) as client:
        response = client.get("/caption-presets", headers={HEADER: "not-the-token"})
    assert response.status_code == 401


def test_protected_endpoint_accepts_correct_token(enforced):
    with TestClient(app) as client:
        response = client.get("/caption-presets", headers={HEADER: API_TOKEN})
    assert response.status_code == 200
    assert len(response.json()) == 22


def test_install_endpoint_is_protected(enforced):
    """The riskiest endpoint - it launches installers - must not be open."""
    with TestClient(app) as client:
        response = client.post("/api/setup/install", json={"component": "ffmpeg"})
    assert response.status_code == 401


def test_enforcement_can_be_disabled_for_headless_use():
    """With KLIPZY_DISABLE_AUTH set (the suite default), requests pass through."""
    assert auth.auth_disabled() is True
    with TestClient(app) as client:
        assert client.get("/caption-presets").status_code == 200


def test_cors_does_not_use_a_wildcard():
    """A wildcard origin would let any website drive this API."""
    origins = None
    for middleware in app.user_middleware:
        if middleware.cls.__name__ == "CORSMiddleware":
            origins = middleware.kwargs.get("allow_origins")
            break
    assert origins is not None, "CORS middleware is not installed"
    assert "*" not in origins
