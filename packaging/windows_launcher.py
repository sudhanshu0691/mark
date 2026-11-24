"""Windows executable launcher for a source-based, locked MARK distribution."""
import subprocess
import sys
from pathlib import Path


def main():
    if getattr(sys, "frozen", False):
        root = Path(sys.executable).resolve().parent
        if root.name.casefold() == "packaging" and not (root / "main.py").is_file():
            root = root.parent
    else:
        root = Path(__file__).resolve().parent.parent
    scripts = root / ".venv" / "Scripts"
    program = root / "main.py"
    python = scripts / "pythonw.exe"
    if sys.argv[1:] == ["--check"]:
        console = scripts / "python.exe"
        if not console.is_file() or not python.is_file() or not program.is_file():
            return 1
        return subprocess.run([str(console), str(program), "--check-startup"], cwd=root, check=False, timeout=60).returncode
    if not python.is_file():
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            None, "Run setup_windows.bat before launching MARK.", "MARK setup required", 0x10,
        )
        return 1
    subprocess.Popen([str(python), str(program)], cwd=root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
