@echo off
setlocal
title Klipzy Studio Launcher
cd /d "%~dp0"

echo ==============================================
echo    Klipzy Studio - One-Click Launcher
echo ==============================================
echo.

REM ---------- [1/5] Check Python ----------
echo [1/5] Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo ERROR: Python not found.
    echo Install Python 3.10+ from https://www.python.org/downloads/
    echo Make sure to tick "Add Python to PATH" during install.
    echo.
    pause
    exit /b 1
)

REM ---------- [2/5] Find / create virtual environment ----------
echo [2/5] Checking virtual environment...
if exist ".venv\Scripts\python.exe" (
    set "VENVPY=.venv\Scripts\python.exe"
) else if exist "venv\Scripts\python.exe" (
    set "VENVPY=venv\Scripts\python.exe"
) else (
    echo Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo ERROR: Failed to create virtual environment.
        pause
        exit /b 1
    )
    set "VENVPY=.venv\Scripts\python.exe"
)

REM ---------- [3/5] Install Python dependencies if missing ----------
echo [3/5] Checking Python dependencies...
"%VENVPY%" -c "import fastapi, uvicorn, whisper" >nul 2>&1
if errorlevel 1 (
    echo Installing Python dependencies (this may take a few minutes)...
    "%VENVPY%" -m pip install --upgrade pip
    if errorlevel 1 (
        echo WARNING: pip upgrade failed, continuing...
    )
    "%VENVPY%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo ERROR: Failed to install Python dependencies.
        pause
        exit /b 1
    )
)

REM ---------- [4/5] Check Node.js and UI dependencies ----------
echo [4/5] Checking Node.js...
node --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo ERROR: Node.js not found.
    echo Install Node.js 18+ from https://nodejs.org/
    echo.
    pause
    exit /b 1
)

if not exist "ui\node_modules" (
    echo Installing UI dependencies...
    pushd ui
    call npm install
    if errorlevel 1 (
        echo.
        echo ERROR: Failed to install UI dependencies.
        popd
        pause
        exit /b 1
    )
    popd
)

REM ---------- [5/5] Launch Klipzy Studio ----------
echo [5/5] Launching Klipzy Studio...
echo.
echo    The desktop app will open shortly.
echo    Close the app window to stop.
echo.
pushd ui
call npm start
popd

echo.
echo Klipzy Studio has exited.
pause