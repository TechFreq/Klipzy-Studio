#!/usr/bin/env python3
"""
Klipzy Studio - local-first Long Form to Shorts studio launcher.

Entry point: `python scripts/main.py`  (equivalent to `python -m server.api.server`)
Launches the FastAPI backend used by the Electron desktop app.

This lives in scripts/ alongside the other launchers. Because it is one level
down from the repo root, it puts the root on sys.path before importing the
`server` package, so `python scripts/main.py` works from any directory.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from server.api.server import start_server

if __name__ == "__main__":
    start_server(host="127.0.0.1", port=8765)
