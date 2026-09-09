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
if errorlevel 1 goto :no_python

REM Present but too old? The ML stack needs 3.10+, and a 3.9 venv fails later
REM with confusing import errors, so check the version up front.
python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if errorlevel 1 goto :old_python

REM ---------- [2/5] Find / create / repair virtual environment ----------
echo [2/5] Checking virtual environment...
set "VENVPY="
if exist ".venv\Scripts\python.exe" set "VENVPY=.venv\Scripts\python.exe"
if not defined VENVPY if exist "venv\Scripts\python.exe" set "VENVPY=venv\Scripts\python.exe"
if not defined VENVPY goto :make_venv

REM A venv copied from another machine (e.g. from macOS) keeps a pyvenv.cfg that
REM points at an interpreter that doesn't exist here, so its python.exe stub
REM fails with "did not find executable at ...". Actually RUN it; if it can't
REM start, wipe it and rebuild for Windows (same idea as the node_modules check).
"%VENVPY%" --version >nul 2>&1
if errorlevel 1 goto :rebuild_venv
goto :deps_check

:rebuild_venv
echo Virtual environment is broken or from another OS - rebuilding for Windows...
if exist ".venv" rmdir /s /q ".venv"
if exist "venv" rmdir /s /q "venv"

:make_venv
echo Creating virtual environment...
python -m venv .venv
if errorlevel 1 goto :venv_failed
set "VENVPY=.venv\Scripts\python.exe"

REM ---------- [3/5] Install Python dependencies if missing ----------
:deps_check
echo [3/5] Checking Python dependencies...
"%VENVPY%" -c "import fastapi, uvicorn, whisper" >nul 2>&1
if errorlevel 1 goto :install_pydeps
goto :node_check

:install_pydeps
echo Installing Python dependencies (this may take a few minutes)...
"%VENVPY%" -m pip install --upgrade pip
"%VENVPY%" -m pip install -r requirements.txt
if errorlevel 1 goto :pydeps_failed

REM ---------- [4/5] Check Node.js and UI dependencies ----------
:package_deps
:node_check
echo [4/5] Checking Node.js...
node --version >nul 2>&1
if errorlevel 1 goto :no_node

if not exist "ui\node_modules" goto :install_ui
if not exist "ui\node_modules\electron\path.txt" goto :reinstall_ui

REM Electron built for macOS/Linux points at "Electron.app/..." instead of an
REM .exe. Running that on Windows fails, so detect it and rebuild for Windows.
findstr /I ".exe" "ui\node_modules\electron\path.txt" >nul 2>&1
if errorlevel 1 goto :reinstall_ui
goto :launch

:reinstall_ui
echo Detected UI dependencies built for a different OS; rebuilding for Windows...
rmdir /s /q "ui\node_modules"

:install_ui
echo Installing UI dependencies...
pushd ui
call npm install
if errorlevel 1 ( popd & goto :ui_failed )
popd

REM ---- Verify Electron actually landed, don't just trust npm's exit code ----
REM npm 12+ blocks dependency install scripts by default. Electron's postinstall
REM is what DOWNLOADS the Electron binary, so npm can report "added 310 packages"
REM and still leave Electron unusable - the app then dies with the cryptic
REM "Electron failed to install correctly". path.txt is written by that
REM postinstall, so its absence is the reliable signal. package.json now carries
REM an allowScripts entry for electron; this retry covers machines where npm
REM config or an older checkout still blocks it.
if exist "ui\node_modules\electron\path.txt" goto :launch
echo.
echo Electron's binary did not download (npm blocked its install script).
echo Repairing...
REM Verified: a plain `npm install` does NOT fix this - npm sees the tree as
REM complete, reports "up to date", and never re-runs the postinstall. `npm
REM rebuild` forces install scripts to run again, which is what actually repairs it.
pushd ui
call npm rebuild electron
popd
if exist "ui\node_modules\electron\path.txt" goto :electron_repaired

REM Last resort: remove just Electron so npm has to genuinely reinstall it
REM (a real install DOES run the postinstall), rather than wiping all 310 packages.
echo Rebuild did not take - reinstalling Electron from scratch...
if exist "ui\node_modules\electron" rmdir /s /q "ui\node_modules\electron"
pushd ui
call npm install --allow-scripts=electron
popd
if exist "ui\node_modules\electron\path.txt" goto :electron_repaired
goto :electron_failed

:electron_repaired
echo Electron repaired successfully.
goto :launch

REM ---------- [5/5] Launch Klipzy Studio ----------
:launch
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
exit /b 0

REM ============ ERROR HANDLERS ============
:no_python
echo.
echo Python was not found on this PC.
echo Klipzy needs Python 3.10 or newer.
echo.
where winget >nul 2>&1
if errorlevel 1 goto :python_manual
choice /c YN /n /m "Install Python 3.12 automatically now? [Y/N] "
if errorlevel 2 goto :python_manual
echo.
echo Installing Python 3.12 (this opens Windows Package Manager)...
winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
if errorlevel 1 goto :python_manual
goto :reopen_needed

:old_python
echo.
for /f "tokens=*" %%v in ('python --version 2^>^&1') do echo Found %%v - too old.
echo Klipzy needs Python 3.10 or newer.
echo.
echo Direct Windows download (pick "Windows installer (64-bit)"):
echo    https://www.python.org/downloads/windows/
echo During setup, TICK "Add python.exe to PATH".
echo.
pause
exit /b 1

:python_manual
echo.
echo Install Python manually - direct Windows download page:
echo    https://www.python.org/downloads/windows/
echo Pick "Windows installer (64-bit)" under the latest 3.12 or 3.13 release.
echo.
echo IMPORTANT: on the first setup screen, TICK "Add python.exe to PATH"
echo before clicking Install, or this launcher won't find it.
echo.
pause
exit /b 1

:no_node
echo.
echo Node.js was not found on this PC.
echo Klipzy needs Node.js 18 or newer (any current LTS is fine).
echo.
where winget >nul 2>&1
if errorlevel 1 goto :node_manual
choice /c YN /n /m "Install the latest Node.js LTS automatically now? [Y/N] "
if errorlevel 2 goto :node_manual
echo.
echo Installing Node.js LTS (this opens Windows Package Manager)...
winget install -e --id OpenJS.NodeJS.LTS --accept-source-agreements --accept-package-agreements
if errorlevel 1 goto :node_manual
goto :reopen_needed

:node_manual
echo.
echo Install Node.js manually - direct Windows download page:
echo    https://nodejs.org/en/download
echo Choose the Windows Installer (.msi, 64-bit). The LTS build is recommended,
echo but any version 18 or newer works.
echo.
pause
exit /b 1

:reopen_needed
echo.
echo ==============================================
echo    Installed. One more step:
echo ==============================================
echo.
echo CLOSE this window, then run start_klipzy.bat again.
echo Windows only shows newly installed programs to a freshly opened window,
echo so this launcher can't see it until you reopen it.
echo.
pause
exit /b 0

:venv_failed
echo.
echo ERROR: Failed to create the Python virtual environment.
echo Try deleting the ".venv" folder in this directory, then run this again.
pause
exit /b 1

:pydeps_failed
echo.
echo ERROR: Failed to install Python dependencies.
echo.
echo Most common causes:
echo   - No internet connection, or a VPN/proxy blocking pypi.org
echo   - Antivirus blocking the install
echo.
echo To start clean: delete the ".venv" folder in this directory and run again.
pause
exit /b 1

:ui_failed
echo.
echo ERROR: Failed to install UI dependencies (npm install).
echo.
echo To start clean: delete the "ui\node_modules" folder and run again.
pause
exit /b 1

:electron_failed
echo.
echo ERROR: Electron did not install correctly.
echo.
echo npm downloaded the packages but blocked Electron's install script, which is
echo what fetches the Electron program itself.
echo.
echo Fix it manually with these commands:
echo    cd /d "%~dp0ui"
echo    npm install-scripts approve electron
echo    npm rebuild electron
echo.
echo Note: plain "npm install" will NOT fix it - npm thinks everything is already
echo installed and skips the step that downloads Electron. "npm rebuild" is the
echo command that forces it.
echo.
echo If that still fails, delete "ui\node_modules" entirely and run this again.
pause
exit /b 1
