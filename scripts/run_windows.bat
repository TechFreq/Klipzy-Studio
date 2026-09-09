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
    echo ERROR: Python not found.
    echo Direct Windows download: https://www.python.org/downloads/windows/
    echo Pick "Windows installer ^(64-bit^)" and TICK "Add python.exe to PATH".
    echo Or run: winget install -e --id Python.Python.3.12
    pause
    exit /b 1
)
python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python 3.10 or newer is required.
    echo Direct Windows download: https://www.python.org/downloads/windows/
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
    echo ERROR: Node.js not found.
    echo Direct Windows download: https://nodejs.org/en/download ^(LTS recommended, 18+^)
    echo Or run: winget install -e --id OpenJS.NodeJS.LTS
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
REM npm 12+ blocks dependency install scripts, and Electron's postinstall is what
REM downloads the Electron binary - so npm can succeed while leaving Electron
REM unusable ("Electron failed to install correctly"). path.txt is written by
REM that postinstall, so verify it rather than trusting npm's exit code.
REM Repair uses `npm rebuild`: a plain `npm install` reports "up to date" and
REM never re-runs the script, so it does NOT fix a broken tree (verified).
if not exist "ui\node_modules\electron\path.txt" (
    echo Electron binary missing - repairing with npm rebuild...
    pushd ui
    call npm rebuild electron
    popd
)
if not exist "ui\node_modules\electron\path.txt" (
    echo.
    echo ERROR: Electron did not install correctly. Fix it with:
    echo    cd /d "%~dp0..\ui"
    echo    npm install-scripts approve electron
    echo    npm rebuild electron
    echo ^(plain "npm install" will NOT fix it - it skips the download step.^)
    pause
    exit /b 1
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
