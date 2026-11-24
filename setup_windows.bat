@echo off
title MARK setup
cd /d "%~dp0"
echo === MARK one-time setup ===
where py >nul 2>nul
if errorlevel 1 (
    echo Python launcher 'py' not found on PATH.
    echo Install Python 3.11 or 3.12 64-bit from https://www.python.org/downloads/windows/
    echo and tick "Add python.exe to PATH" during install.
    pause
    exit /b 1
)
set PYV=
py -3.11 --version >nul 2>nul && set PYV=3.11
if not defined PYV py -3.12 --version >nul 2>nul && set PYV=3.12
if not defined PYV (
    echo Python 3.11 or 3.12 is required (you may have 3.13+ only^).
    echo Install Python 3.11.9 64-bit from https://www.python.org/downloads/windows/
    pause
    exit /b 1
)
echo Using Python %PYV%
if not exist .venv (
    echo Creating virtual environment...
    py -%PYV% -m venv .venv
    if errorlevel 1 (
        echo.
        echo FAILED to create .venv. Usual causes: antivirus blocking it, or a broken Python install.
        echo Try manually:  py -%PYV% -m venv .venv
        pause
        exit /b 1
    )
)
set "VENV_PY=%~dp0.venv\Scripts\python.exe"
"%VENV_PY%" --version >nul 2>nul
if errorlevel 1 (
    echo Existing .venv is stale or broken. Recreating it...
    py -%PYV% -m venv --clear .venv
    if errorlevel 1 (
        echo FAILED to recreate the virtual environment.
        pause
        exit /b 1
    )
)
if not exist "%VENV_PY%" (
    echo FAILED to find the venv interpreter. Try deleting the '.venv' folder and re-run this file.
    pause
    exit /b 1
)
echo Using venv interpreter: %VENV_PY%
"%VENV_PY%" setup.py
set RC=%errorlevel%
if not %RC%==0 (
    echo.
    echo Setup did not finish cleanly ^(exit %RC%^). Run this for a health report:
    echo     python setup.py --doctor
    pause
    exit /b %RC%
)
where winget >nul 2>nul
if errorlevel 1 echo NOTE: winget not found - install "App Installer" from Microsoft Store so MARK can install apps.
echo.
echo Configure Groq and Gemini credentials before starting MARK.
echo Done. Read SETUP.md for Windows settings, then double-click run_mark.bat
pause

