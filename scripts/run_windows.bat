@echo off
REM Klipzy Studio - Windows launcher
REM Sets up the Python venv + UI deps if needed, then launches the desktop app
REM (which spawns the Python backend itself). Run from anywhere.

setlocal
title Klipzy Studio Launcher

REM Repo root is the PARENT of this scripts\ folder - NOT the scripts folder.
cd /d "%~dp0.."

echo [1/4] Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Install Python 3.10+ from https://www.python.org/downloads/
    echo Make sure to tick "Add Python to PATH" during install.
    pause
    exit /b 1
)

echo [2/4] Checking virtual environment...
set "VENVPY=.venv\Scripts\python.exe"
if not exist "%VENVPY%" (
    if exist "venv\Scripts\python.exe" (
        set "VENVPY=venv\Scripts\python.exe"
    ) else (
        echo Creating virtual environment and installing dependencies...
        python -m venv .venv
        if errorlevel 1 ( echo ERROR: could not create venv & pause & exit /b 1 )
        ".venv\Scripts\python.exe" -m pip install --upgrade pip
        ".venv\Scripts\python.exe" -m pip install -r requirements.txt
        if errorlevel 1 ( echo ERROR: dependency install failed & pause & exit /b 1 )
    )
)

echo [3/4] Checking Node.js and UI dependencies...
node --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Node.js not found. Install Node.js 18+ from https://nodejs.org/
    pause
    exit /b 1
)
if not exist "ui\node_modules" (
    echo Installing UI dependencies...
    pushd ui
    call npm install
    if errorlevel 1 ( echo ERROR: npm install failed & popd & pause & exit /b 1 )
    popd
)

echo [4/4] Launching Klipzy Studio desktop app...
echo    The app window will open shortly. Close it to stop.
pushd ui
call npm start
popd

echo.
echo Klipzy Studio has exited.
pause
exit /b 0
