"""Run the regression suite and propagate its actual exit status."""
import subprocess
import sys
from pathlib import Path

if __name__ == "__main__":
    raise SystemExit(subprocess.call([sys.executable, "-m", "pytest", "-c", "tooling/pyproject.toml", "tests", "-q", *sys.argv[1:]], cwd=Path(__file__).resolve().parents[1]))
