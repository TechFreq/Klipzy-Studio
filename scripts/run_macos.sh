#!/usr/bin/env bash
# Klipzy Studio - macOS / Linux launcher.
# Sets up the Python venv + UI deps if needed, then launches the desktop app
# (which spawns the Python backend itself).
set -e

# Repo root is the PARENT of this scripts/ folder - NOT the scripts folder.
cd "$(dirname "$0")/.."

echo "[1/4] Checking Python..."
if ! command -v python3 &> /dev/null; then
    echo "ERROR: Python not found. Install Python 3.10+."
    exit 1
fi

echo "[2/4] Checking virtual environment..."
if [ -f ".venv/bin/python" ]; then
    VENV=".venv"
elif [ -f "venv/bin/python" ]; then
    VENV="venv"
else
    echo "Creating virtual environment and installing dependencies..."
    python3 -m venv .venv
    VENV=".venv"
    "./$VENV/bin/python" -m pip install --upgrade pip
    "./$VENV/bin/python" -m pip install -r requirements.txt
fi

echo "[3/4] Checking Node.js and UI dependencies..."
if ! command -v node &> /dev/null; then
    echo "ERROR: Node.js not found. Install Node.js 18+ from https://nodejs.org/"
    exit 1
fi
if [ ! -d "ui/node_modules" ]; then
    echo "Installing UI dependencies..."
    ( cd ui && npm install )
fi

echo "[4/4] Launching Klipzy Studio desktop app..."
echo "   The app window will open shortly. Close it to stop."
( cd ui && npm start )
