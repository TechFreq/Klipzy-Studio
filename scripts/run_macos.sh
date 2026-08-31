#!/usr/bin/env bash
# Klipzy Studio - macOS / Linux launcher.
# Sets up the Python venv + UI deps if needed, then launches the desktop app
# (which spawns the Python backend itself).
#
# This launcher is OS-aware: if the project was copied from another machine
# (e.g. Windows) and carries dependencies built for that OS, it detects the
# mismatch and rebuilds them automatically instead of crashing.
set -e

# Repo root is the PARENT of this scripts/ folder - NOT the scripts folder.
cd "$(dirname "$0")/.."

echo "[1/4] Checking Python..."
if ! command -v python3 &> /dev/null; then
    echo "ERROR: Python not found. Install Python 3.10+."
    exit 1
fi

echo "[2/4] Checking virtual environment..."
VENV=""
if [ -f ".venv/bin/python" ]; then
    VENV=".venv"
elif [ -f "venv/bin/python" ]; then
    VENV="venv"
elif [ -d ".venv" ] || [ -d "venv" ]; then
    # A venv folder exists but has no macOS/Linux "bin/python" - it was almost
    # certainly built on Windows (which uses "Scripts/python.exe"). Rebuild it.
    echo "Existing virtual environment was built for a different OS; rebuilding..."
    rm -rf .venv venv
fi

if [ -z "$VENV" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
    VENV=".venv"
fi

# Make sure the Python dependencies are actually present (covers a fresh venv
# AND an existing venv that is missing packages).
echo "Checking Python dependencies..."
if ! "./$VENV/bin/python" -c "import fastapi, uvicorn, whisper" >/dev/null 2>&1; then
    echo "Installing Python dependencies (this may take a few minutes)..."
    "./$VENV/bin/python" -m pip install --upgrade pip
    "./$VENV/bin/python" -m pip install -r requirements.txt
fi

echo "[3/4] Checking Node.js and UI dependencies..."
if ! command -v node &> /dev/null; then
    echo "ERROR: Node.js not found. Install Node.js 18+ from https://nodejs.org/"
    exit 1
fi

NEED_UI_INSTALL=0
if [ ! -d "ui/node_modules" ]; then
    NEED_UI_INSTALL=1
elif [ ! -f "ui/node_modules/electron/path.txt" ]; then
    # node_modules exists but Electron looks incomplete - reinstall to be safe.
    echo "UI dependencies look incomplete; reinstalling..."
    rm -rf ui/node_modules
    NEED_UI_INSTALL=1
elif grep -qi '\.exe' "ui/node_modules/electron/path.txt"; then
    # Electron was built for Windows (path.txt points to electron.exe).
    # Running it on macOS/Linux fails with "Permission denied" (exit 126),
    # so wipe it and let npm fetch the correct build for this OS.
    echo "Detected Windows-built Electron in node_modules; rebuilding for this OS..."
    rm -rf ui/node_modules
    NEED_UI_INSTALL=1
fi

if [ "$NEED_UI_INSTALL" = "1" ]; then
    echo "Installing UI dependencies..."
    ( cd ui && npm install )
fi

echo "[4/4] Launching Klipzy Studio desktop app..."
echo "   The app window will open shortly. Close it to stop."
( cd ui && npm start )
