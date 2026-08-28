#!/usr/bin/env bash
# AI Video Clipper - macOS/Linux launcher
cd "$(dirname "$0")"

echo "[1/3] Checking Python..."
if ! command -v python3 &> /dev/null; then
    echo "ERROR: Python not found. Install Python 3.10+"
    exit 1
fi

echo "[2/3] Checking venv..."
if [ ! -f "venv/bin/python" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
else
    source venv/bin/activate
fi

echo "[3/3] Starting AI Video Clipper..."
python -m server.api.server