@echo off
:: Full control mode: starts MARK with administrator rights (protected files, all-user installs)
cd /d "%~dp0"
set "PYTHON_DIR=.venv"
if not exist "%PYTHON_DIR%\Scripts\pythonw.exe" set "PYTHON_DIR=venv"
if not exist "%PYTHON_DIR%\Scripts\pythonw.exe" (
    echo ERROR: no Python environment was found. Run setup_windows.bat first.
    pause
    exit /b 1
)
powershell -NoProfile -Command "Start-Process -Verb RunAs -FilePath '%~dp0%PYTHON_DIR%\Scripts\pythonw.exe' -ArgumentList 'main.py' -WorkingDirectory '%~dp0'"
if errorlevel 1 (
    echo UAC was declined or admin start failed.
    pause
)

