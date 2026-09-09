@echo off
setlocal enabledelayedexpansion
title Klipzy Studio - Electron Repair
cd /d "%~dp0"

echo ==============================================
echo    Klipzy Studio - Electron Repair Tool
echo ==============================================
echo.
echo This fixes the "Electron failed to install correctly" error.
echo.
echo What causes it: npm 12 stopped running dependency install scripts by
echo default. Electron's install script is the part that DOWNLOADS Electron
echo itself, so npm reports success while leaving the program missing.
echo.

set "ELEC=ui\node_modules\electron"

REM ---------- Diagnostics first, so a failure report is actually useful ----------
echo ---------- Diagnostics ----------
for /f "tokens=*" %%v in ('node --version 2^>^&1') do echo node    : %%v
for /f "tokens=*" %%v in ('npm --version 2^>^&1') do echo npm     : %%v
if exist "ui\node_modules" (echo node_modules : present) else (echo node_modules : MISSING)
if exist "%ELEC%" (echo electron pkg : present) else (echo electron pkg : MISSING)
if exist "%ELEC%\install.js" (echo install.js   : present) else (echo install.js   : MISSING)
if exist "%ELEC%\path.txt" (echo path.txt     : present ^(already installed?^)) else (echo path.txt     : MISSING ^(this is the problem^))
if exist "%ELEC%\dist\electron.exe" (echo electron.exe : present) else (echo electron.exe : MISSING)
echo ---------------------------------
echo.

if not exist "%ELEC%\path.txt" goto :repair
if not exist "%ELEC%\dist\electron.exe" goto :repair
echo Electron already looks correctly installed.
echo If the app still won't start, delete "ui\node_modules" and run
echo start_klipzy.bat again.
echo.
pause
exit /b 0

:repair
echo Starting repair. This downloads about 170 MB - please be patient.
echo.

REM ---- Attempt 1: run Electron's own downloader directly (npm can't block it) ----
if not exist "%ELEC%\install.js" goto :attempt2
echo [1/3] Running Electron's own downloader (node install.js)...
pushd "%ELEC%"
call node install.js
popd
if exist "%ELEC%\path.txt" goto :fixed
echo       ...did not produce the expected files.
echo.

:attempt2
if exist "%ELEC%\install.js" goto :attempt2_run
echo [1/3] Skipped: install.js is missing from the electron package.
echo.
:attempt2_run
echo [2/3] Trying: npm rebuild electron
pushd ui
call npm rebuild electron
popd
if exist "%ELEC%\path.txt" goto :fixed
echo       ...npm reported success but the files are still missing.
echo.

REM ---- Attempt 3: remove ONLY electron and reinstall it ----
echo [3/3] Removing Electron and reinstalling it cleanly...
if exist "%ELEC%" rmdir /s /q "%ELEC%"
pushd ui
call npm install
popd
if exist "%ELEC%\path.txt" goto :fixed

REM ---- Attempt 4: last resort, wipe everything and start over ----
echo.
echo [4/4] Last resort: reinstalling ALL UI dependencies from scratch...
if exist "ui\node_modules" rmdir /s /q "ui\node_modules"
pushd ui
call npm install
popd
if exist "%ELEC%\path.txt" goto :fixed
goto :failed

:fixed
echo.
echo ==============================================
echo    SUCCESS - Electron is installed
echo ==============================================
if exist "%ELEC%\dist\electron.exe" (echo Verified: electron.exe is on disk.) else (echo Note: path.txt exists but electron.exe was not found.)
echo.
echo You can now run start_klipzy.bat to launch the app.
echo.
pause
exit /b 0

:failed
echo.
echo ==============================================
echo    Repair did not succeed
echo ==============================================
echo.
echo Every method failed, which usually means the download itself is being
echo blocked rather than npm misbehaving. Check:
echo.
echo   1. Internet connection ^(the download is ~170 MB from github.com^)
echo   2. Antivirus / firewall blocking node.exe
echo   3. A company VPN or proxy. If you use a proxy, set it for npm:
echo        npm config set proxy http://your-proxy:port
echo        npm config set https-proxy http://your-proxy:port
echo   4. Corporate machine policies blocking script execution
echo.
echo Manual download alternative:
echo   Get electron-v28.3.3-win32-x64.zip from
echo   https://github.com/electron/electron/releases/tag/v28.3.3
echo   Extract it into: %~dp0ui\node_modules\electron\dist\
echo   Then create a file  %~dp0ui\node_modules\electron\path.txt
echo   containing exactly:  electron.exe
echo.
echo Please share everything above ^(including the Diagnostics section^)
echo when reporting this.
echo.
pause
exit /b 1
