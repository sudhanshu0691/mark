# MARK setup — installs declared dependencies and checks available features.
#
# Usage:
#   python setup.py                 (inside a venv is best, plain python also works)
#   setup_windows.bat               (one-click: creates venv + calls this file)
#   python setup.py --doctor        (diagnose only: no installs, prints a health report)
import argparse
import importlib
import subprocess
import sys
import platform
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
IS_WIN = platform.system() == "Windows"

# module import name -> (friendly name, is_optional)
WANT = [
    ("PySide6", "desktop UI", False),
    ("openpyxl", "Excel workbook editing", False),
    ("keyring", "OS credential storage", False),
    ("numpy", "UI math", False),
    ("sounddevice", "microphone", False),
    ("groq", "Groq SDK", False),
    ("google.genai", "Gemini SDK for the voice runtime", False),
    ("psutil", "system telemetry", False),
    ("requests", "web calls", False),
    ("edge_tts", "TTS", False),
    ("PIL", "images (Pillow)", False),
    ("mss", "screen capture", False),
    ("cv2", "screen/vision", False),
    ("pynvml", "GPU telemetry", True),
    ("pyautogui", "UI automation", False),
    ("pyperclip", "clipboard", False),
    ("pptx", "PowerPoint (ppt_studio)", False),
    ("docx", "Word", False),
    ("pypdf", "PDF editing", False),
    ("pypdfium2", "PDF page rendering", False),
    ("pydub", "audio editing", True),
    ("playwright", "browser automation", True),
    ("pytesseract", "OCR binding", True),
    ("win32com.client", "Windows COM", True),
    ("wmi", "hardware sensors", True),
    ("comtypes", "volume control", True),
    ("livekit.rtc", "LiveKit voice transport", True),
    ("livekit.api", "LiveKit room authentication", True),
    ("faster_whisper", "local speech recognition", True),
    ("torch", "local computer vision backend", True),
    ("ultralytics", "vision detection", True),
    ("dill", "trusted local vision checkpoint compatibility", True),
    ("winocr", "Windows OCR", True),
]

OPTIONAL_NOTES = {
    "pydub": "needs the ffmpeg binary for full formats: winget install Gyan.FFmpeg",
    "pytesseract": "needs the Tesseract ENGINE too (binding installs without it!)",
    "wmi": "missing => temperature sensors unavailable (battery/disk still work)",
    "playwright": "missing => browser_control falls back to native-browser mode",
    "pynvml": "missing => GPU % reads as N/A",
    "livekit.rtc": "missing => install the livekit extra with uv",
    "livekit.api": "missing => install the livekit extra with uv",
    "torch": "missing => local computer vision backend unavailable",
    "winocr": "missing => install a Tesseract engine for OCR instead",
}

def _gemini_models():
    try:
        from core.llm import load_config
        cfg = load_config().get("gemini", {})
        return cfg.get("text_model", ""), cfg.get("live_model", "")
    except Exception:
        return "", ""


def _run(exe, *a, timeout=600):
    return subprocess.run([str(exe), *a], timeout=timeout)


def pip_install(exe, reqs: Path) -> tuple[bool, str]:
    """Install everything; return (ok, log_tail). Never hides the real error."""
    r = _run(exe, "-m", "pip", "install", "--upgrade", "pip")
    if r.returncode != 0:
        return False, f"pip upgrade failed (exit {r.returncode})"
    constraints = ["-c", str(HERE / "constraints-windows-py311.txt")] if IS_WIN and sys.version_info[:2] == (3, 11) else []
    r = _run(exe, "-m", "pip", "install", *constraints, "-r", str(reqs))
    if r.returncode != 0:
        return False, (
            f"pip install failed (exit {r.returncode}).\n"
            "  Common causes on this machine:\n"
            "   - a native package needs a C/Rust compiler (check the error above),\n"
            "   - you are offline or on a proxy (set HTTPS_PROXY),\n"
            "   - pip cache is corrupt (pip cache purge).\n"
            f"  Rerun manually to see the exact line: \"{exe}\" -m pip install -r requirements.txt"
        )
    return True, ""


def verify() -> tuple[list[str], list[str]]:
    """Import-check every dependency. Returns (missing_required, missing_optional)."""
    req, opt = [], []
    for mod, friendly, optional in WANT:
        try:
            got = importlib.import_module(mod)
            if mod == "pynvml" and not hasattr(got, "nvmlDeviceGetUtilizationRates"):
                raise ImportError("pynvml shim without NVML bindings (use nvidia-ml-py)")
        except Exception as e:
            (opt if optional else req).append(f"{friendly} ({mod}): {type(e).__name__}: {e}")
    return req, opt


def report_capabilities():
    import shutil
    import sqlite3

    from actions.office_files import OFFICE_APPS, libreoffice_path, office_available

    for program in sorted(set(OFFICE_APPS.values())):
        print(f"{program}: {'registered' if office_available(program) else 'optional, not detected'}")
    for label, available in (
        ("LibreOffice", libreoffice_path()), ("Tesseract OCR", shutil.which("tesseract")),
        ("FFmpeg", shutil.which("ffmpeg")),
    ):
        print(f"{label}: {'found' if available else 'optional, not detected'}")
    try:
        import sounddevice
        devices = sounddevice.query_devices()
        inputs = sum(item["max_input_channels"] > 0 for item in devices)
        outputs = sum(item["max_output_channels"] > 0 for item in devices)
        print(f"Audio devices: {inputs} input, {outputs} output")
    except Exception as exc:
        print(f"Audio discovery unavailable ({type(exc).__name__})")
    try:
        import mss
        with mss.MSS() as capture:
            print(f"Displays detected: {max(0, len(capture.monitors) - 1)}")
    except Exception as exc:
        print(f"Display discovery unavailable ({type(exc).__name__})")
    with sqlite3.connect(":memory:") as database:
        try:
            database.execute("CREATE VIRTUAL TABLE test USING fts5(value)")
            print("SQLite memory: FTS5 available")
        except sqlite3.Error:
            print("SQLite memory: fallback retrieval available")
    try:
        from core.secrets import get_secret
        for provider in ("groq", "gemini"):
            print(f"{provider.title()} credential: {'configured' if get_secret(provider) else 'not configured'}")
    except Exception as exc:
        print(f"Credential store could not be queried ({type(exc).__name__})")


def doctor() -> int:
    print("=== MARK preflight ===")
    print(f"python: {sys.version.split()[0]}  exe: {sys.executable}")
    print(f"venv:   {'YES' if sys.prefix != sys.base_prefix else 'NO (runs, but a venv is safer)'}")
    print(f"platform: {platform.system()} {platform.release()}")
    req, opt = verify()
    if req:
        print("\nMISSING (MARK will NOT start):")
        for m in req:
            print(f"  ! {m}")
    else:
        print("\nAll required packages import OK.")
    if opt:
        print("\nMissing OPTIONAL features (MARK will report these capabilities unavailable):")
        for m in opt:
            print(f"  ? {m}")
    cfg = HERE / "config"
    print(f"\nconfig/api_keys.json: {'legacy profile present' if (cfg / 'api_keys.json').exists() else 'optional; OS credential store is preferred'}")
    print(f"plugins/: {len(list((HERE / 'plugins').glob('*.py')))} python files")
    report_capabilities()
    supported = (3, 11) <= sys.version_info[:2] < (3, 13) and sys.maxsize > 2**32
    if not supported:
        print("MARK requires 64-bit Python 3.11 or 3.12.")
    return 0 if not req and supported else 1


def test_gemini() -> int:
    """Make one authenticated text request using the configured key."""
    try:
        from core.secrets import get_secret
        api_key = get_secret("gemini")
    except Exception as e:
        print(f"Gemini test failed: credential store error ({type(e).__name__})")
        return 1
    if not api_key:
        print("Gemini test skipped: no Gemini credential is configured.")
        print("Save a key through the MARK API KEYS control or set GEMINI_API_KEY.")
        return 1
    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        text_model, live_model = _gemini_models()
        if not text_model:
            print("Gemini test failed: text model is not configured in config/llm.json.")
            return 1
        response = client.models.generate_content(
            model=text_model,
            contents="Reply with exactly: MARK API OK",
        )
        text = (response.text or "").strip()
        if not text:
            print("Gemini test failed: the API returned an empty response.")
            return 1
        print(f"Gemini text API OK: {text[:120]}")
        print(f"Gemini Live model configured: {live_model or 'not configured'}")
        return 0
    except Exception as e:
        print(f"Gemini test failed ({type(e).__name__}): {e}")
        return 1

def main() -> int:
    import argparse as _ap
    ap = _ap.ArgumentParser()
    ap.add_argument("--dependencies-ready", action="store_true", help="skip dependency install after uv sync")
    ap.add_argument("--doctor", action="store_true", help="diagnose only, install nothing")
    ap.add_argument("--test-gemini", action="store_true", help="test the configured Gemini API key")
    ap.add_argument("--no-playwright", action="store_true", help="skip the ~170MB chromium download")
    ap.add_argument("--offline-speech", action="store_true", help="download only the configured multilingual speech model")
    args = ap.parse_args()
    if args.offline_speech:
        from actions.phone.speech import load_config
        from faster_whisper.utils import download_model
        name = load_config()["whisper_model"]
        print(f"Installing configured offline speech model {name}...")
        download_model(name)
        print("Offline speech model ready.")
        return 0

    if args.doctor:
        return doctor()
    if args.test_gemini:
        return test_gemini()

    if sys.version_info < (3, 11) or sys.version_info >= (3, 13):
        print(f"Python {sys.version.split()[0]} is not supported. Install Python 3.11 or 3.12 (64-bit).")
        return 1
    if sys.prefix == sys.base_prefix:
        print("NOTE: running outside a venv. Continuing anyway (the .bat normally makes one).")

    if not args.dependencies_ready:
        print("Installing requirements...")
        ok, why = pip_install(sys.executable, HERE / "requirements.txt")
        if not ok:
            print(f"\nINSTALL FAILED:\n{why}")
            return 1

    if not args.no_playwright:
        print("Installing Playwright browser (chromium only, ~170MB)...")
        r = _run(sys.executable, "-m", "playwright", "install", "chromium")
        if r.returncode != 0:
            print("  (chromium download failed - browser_control still works in native mode. "
                   "Retry: playwright install chromium)")
    else:
        print("Skipping chromium (--no-playwright).")

    if IS_WIN:
        try:
            import win32com.client  # noqa: F401
        except ImportError:
            post = Path(sys.executable).parent / "pywin32_postinstall.py"
            print(
                "\npywin32 post-install did not run - desktop shortcuts may be slow/fallback.\n"
                "    Fix manually with:\n"
                f'    "{sys.executable}" -m pip install --force-reinstall pywin32\n'
                f'    "{sys.executable}" "{post}" -install\n'
            )
    print()
    return doctor()


if __name__ == "__main__":
    sys.exit(main())
