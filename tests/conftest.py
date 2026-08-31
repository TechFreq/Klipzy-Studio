"""
Shared pytest configuration.

The local API requires an X-Klipzy-Token header (see server/auth.py). The
existing test suite drives the app through FastAPI's TestClient without that
header, so enforcement is switched off for the whole session here. Tests that
specifically exercise the auth layer re-enable it themselves - see
tests/test_auth.py.

This must run before server.api.server is imported, because the module resolves
its token at import time.
"""

import os

os.environ.setdefault("KLIPZY_DISABLE_AUTH", "1")
