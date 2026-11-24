@echo off
setlocal
title MARK
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo ERROR: MARK environment missing. Run setup_windows.bat first.
    exit /b 1
)
if not exist "data" mkdir "data"
if /i "%~1"=="--console" (
    ".venv\Scripts\python.exe" main.py
    exit /b
)
".venv\Scripts\python.exe" -c "import sys; assert (3,11) <= sys.version_info[:2] < (3,13)"
if errorlevel 1 (
    echo ERROR: Unsupported Python runtime. Run setup_windows.bat.
    exit /b 1
)
start "MARK" /b ".venv\Scripts\pythonw.exe" "%~dp0main.py"
if errorlevel 1 (
    echo MARK launch failed. Run run_mark.bat --console for diagnostics.
    exit /b 1
)
exit /b 0
