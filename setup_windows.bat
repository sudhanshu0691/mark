@echo off
setlocal
title MARK setup
cd /d "%~dp0"
where uv >nul 2>nul
if errorlevel 1 (
    echo uv is required. Install using: winget install --id astral-sh.uv -e
    echo Then reopen the terminal and run setup_windows.bat again.
    pause
    exit /b 1
)
uv sync --python 3.11 --extra livekit --extra vision --no-dev
if errorlevel 1 (
    echo Dependency setup failed. See the uv error above.
    pause
    exit /b 1
)
uv run --no-sync python setup.py --dependencies-ready
if errorlevel 1 (
    echo MARK preflight failed.
    pause
    exit /b 1
)
echo Configure Groq and Gemini credentials using MARK or config\runtime.env.
echo Run run_mark.bat to start MARK. Use run_mark.bat --console for diagnostics.
pause
