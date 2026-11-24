@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo Run setup_windows.bat before enabling autostart.
    exit /b 1
)
set "MARK_ROOT=%~dp0"
powershell -NoProfile -Command "$root=$env:MARK_ROOT; $s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path ([Environment]::GetFolderPath('Startup')) 'MARK.lnk')); $s.TargetPath=(Join-Path $root '.venv\Scripts\pythonw.exe'); $s.Arguments='"'+(Join-Path $root 'main.py')+'"'; $s.WorkingDirectory=$root; $s.Save()"
if errorlevel 1 (
    echo Autostart setup failed.
    exit /b 1
)
echo MARK will start automatically at login.
