@echo off
:: Puts a shortcut in the Startup folder so MARK starts when you log in
cd /d "%~dp0"
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Startup')+'\MARK.lnk'); $s.TargetPath='%~dp0run_mark.bat'; $s.WorkingDirectory='%~dp0'; $s.WindowStyle=7; $s.Save()"
echo MARK will now start automatically at login.
pause

