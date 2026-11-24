@echo off
title MARK
cd /d "%~dp0"
set "PYTHON_DIR=.venv"
if not exist "%PYTHON_DIR%\Scripts\python.exe" set "PYTHON_DIR=venv"
if not exist "%PYTHON_DIR%\Scripts\python.exe" (
    echo ERROR: no Python environment was found. Run setup_windows.bat first.
    pause
    exit /b 1
)
if not exist config\api_keys.json (
    echo NOTE: config\api_keys.json is missing - first start may ask for the optional Gemini voice key.
)
start "MARK" "%PYTHON_DIR%\Scripts\pythonw.exe" main.py
if errorlevel 1 (
    echo Failed to start pythonw. Showing the real error in a console:
    "%PYTHON_DIR%\Scripts\python.exe" main.py
    pause
)
