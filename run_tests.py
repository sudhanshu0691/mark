import subprocess, sys
r = subprocess.run([sys.executable, "-m", "pytest", "tests", "-q"], capture_output=True, text=True)
sys.stdout.buffer.write(r.stdout)
sys.stderr.buffer.write(r.stderr)
open("pytest_baseline.txt", "wb").write((r.stdout + r.stderr).encode("utf-8", "replace"))
print("RC", r.returncode)
