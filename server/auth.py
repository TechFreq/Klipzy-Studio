"""
Local API access control for Klipzy Studio.

The server listens on 127.0.0.1, but "listening on localhost" is NOT a security
boundary for a desktop app: any web page the user happens to open in any browser
can issue cross-origin requests to http://127.0.0.1:8765. Since this API can
kick off installers (/api/setup/install) and read/write arbitrary paths on disk,
an unauthenticated open port is a real drive-by risk.

Two layers guard against that:

1. A shared secret. Electron generates a token at launch, hands it to Python via
   the KLIPZY_API_TOKEN environment variable, and gives the same value to the
   renderer through the preload bridge. Every request must present it in the
   X-Klipzy-Token header. A browser on some random website cannot know it.

2. A strict CORS allowlist (configured in server.api.server) instead of "*".

Headless / scripted use (see INSTRUCTIONS.md "REST API & Headless Mode"): when
no token is supplied by the parent process, one is generated and written to
logs/api_token.txt so local scripts can read it. Set KLIPZY_DISABLE_AUTH=1 to
turn enforcement off entirely - intended for the test suite and for users who
knowingly accept the risk.
"""

from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger("klipzy")

TOKEN_HEADER = "X-Klipzy-Token"
TOKEN_ENV_VAR = "KLIPZY_API_TOKEN"
DISABLE_ENV_VAR = "KLIPZY_DISABLE_AUTH"

# Endpoints reachable without a token. /health is used by the Electron launcher
# to poll for readiness, and the docs routes are harmless read-only pages that
# make the interactive API explorer usable.
PUBLIC_PATHS = frozenset({
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/docs/oauth2-redirect",
})


def auth_disabled() -> bool:
    """True when token enforcement is explicitly turned off."""
    return os.environ.get(DISABLE_ENV_VAR, "").strip().lower() in {"1", "true", "yes"}


def _token_file() -> Path:
    return Path(__file__).resolve().parent.parent / "logs" / "api_token.txt"


def resolve_token() -> str:
    """
    Return the shared secret for this run.

    Prefers a token handed down by the parent process (Electron), otherwise
    generates one. Persists the effective token for desktop reconnects and scripts.
    """
    supplied = os.environ.get(TOKEN_ENV_VAR, "").strip()
    # Persist the effective token for reconnecting desktop instances too.
    token = supplied or secrets.token_urlsafe(32)
    os.environ[TOKEN_ENV_VAR] = token
    if auth_disabled():
        return token  # Tests/headless auth-off runs must not replace a live token file.

    try:
        path = _token_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(token, encoding="utf-8")
        try:
            # Owner-only where the platform honours it (no-op on Windows).
            os.chmod(path, 0o600)
        except OSError:
            pass
        logger.info("Stored the local API token for reconnecting clients at %s", path)
    except OSError as exc:
        logger.warning("Could not persist the API token: %s", exc)

    return token


class LocalTokenAuthMiddleware(BaseHTTPMiddleware):
    """Reject requests that do not carry the shared secret."""

    def __init__(self, app, token: str):
        super().__init__(app)
        self._token = token

    async def dispatch(self, request, call_next):
        if auth_disabled():
            return await call_next(request)

        # CORS preflight carries no custom headers by design; the CORS
        # middleware answers it and the real request is still checked.
        if request.method == "OPTIONS":
            return await call_next(request)

        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)

        presented = request.headers.get(TOKEN_HEADER, "")
        # Constant-time compare so a caller cannot time-probe the secret.
        if not presented or not secrets.compare_digest(presented, self._token):
            logger.warning(
                "Rejected unauthenticated %s %s from %s",
                request.method,
                request.url.path,
                request.client.host if request.client else "unknown",
            )
            return JSONResponse(
                status_code=401,
                content={
                    "detail": (
                        "Missing or invalid API token. Klipzy Studio's local API "
                        f"requires the {TOKEN_HEADER} header. The desktop app sends "
                        "this automatically; scripts can read the value from "
                        "logs/api_token.txt."
                    )
                },
            )

        return await call_next(request)
