"""Windows executable launcher for a source-based, locked MARK distribution."""
import os
from pathlib import Path
import subprocess
import sys

def main():
    root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
    python = root / ".venv" / "Scripts" / "pythonw.exe"
    if not python.is_file():
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, "Run setup_windows.bat before launching MARK.", "MARK setup required", 0x10)
        return 1
    subprocess.Popen([str(python), str(root / "main.py")], cwd=root)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
