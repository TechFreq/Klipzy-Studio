#!/usr/bin/env python3
"""
Clippy Studio - local-first AI video clipper launcher.

Entry point: `python main.py`  (equivalent to `python -m server.api.server`)
Launches the FastAPI backend used by the Electron desktop app.
"""

from server.api.server import start_server

if __name__ == "__main__":
    start_server(host="127.0.0.1", port=8765)