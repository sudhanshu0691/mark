from __future__ import annotations

import io
import json
import re
import sys
from pathlib import Path
from typing import Any

import numpy as np

cv2: Any = None
mss: Any = None
PIL: Any = None

try:
    import cv2
    _CV2 = True
except ImportError:
    _CV2 = False

try:
    import mss
    import mss.tools
    _MSS = True
except ImportError:
    _MSS = False

try:
    import PIL.Image
    _PIL = True
except ImportError:
    _PIL = False


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


_BASE        = _base_dir()
_CONFIG_PATH = _BASE / "config" / "api_keys.json"


def _load_config() -> dict:
    try:
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_config_key(key: str, value) -> None:
    try:
        cfg = _load_config()
        cfg[key] = value
        _CONFIG_PATH.write_text(json.dumps(cfg, indent=4), encoding="utf-8")
    except Exception as e:
        print(f"[Vision] ⚠️  Could not save config key '{key}': {e}")


def _get_os() -> str:
    return _load_config().get("os_system", "windows").lower()

_IMG_MAX_W = 1280
_IMG_MAX_H = 720
_JPEG_Q    = 82
_OCR_MAX_CHARS = 4000

def _compress(img_bytes: bytes, source_format: str = "PNG") -> tuple[bytes, str]:
    if not _PIL:
        return img_bytes, f"image/{source_format.lower()}"

    try:
        img = PIL.Image.open(io.BytesIO(img_bytes)).convert("RGB")
        img.thumbnail((_IMG_MAX_W, _IMG_MAX_H), PIL.Image.Resampling.BILINEAR)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=_JPEG_Q, optimize=False)
        return buf.getvalue(), "image/jpeg"
    except Exception as e:
        print(f"[Vision] ⚠️  Image compress failed: {e}")
        return img_bytes, f"image/{source_format.lower()}"

def _capture_screen() -> tuple[bytes, str]:
    return _capture_screen_region()


def _capture_screen_region(
    monitor: int = 1,
    region: dict | None = None,
) -> tuple[bytes, str]:

    if not _MSS:
        raise RuntimeError("mss is not installed. Run: pip install mss")

    with mss.MSS() as sct:
        monitors = sct.monitors          # [0] = all combined, [1..n] = real screens
        if monitor < 0 or monitor >= len(monitors):
            raise ValueError(f"monitor must be between 0 and {len(monitors) - 1}")
        target = dict(monitors[monitor])
        if region:
            for key in ("left", "top", "width", "height"):
                if key in region:
                    target[key] = int(region[key])
            if target["width"] <= 0 or target["height"] <= 0:
                raise ValueError("region width and height must be positive")
        shot     = sct.grab(target)
        png      = mss.tools.to_png(shot.rgb, shot.size)

    return _compress(png, "PNG")


def ocr_pil(image) -> str:
    """OCR a PIL image. Tries Tesseract, then the built-in Windows OCR (pip install winocr)."""
    err = ""
    # Prefer Windows built-in OCR (winocr) since it needs no external engine.
    try:
        from importlib import import_module
        winocr = import_module("winocr")
        res = winocr.recognize_pil_sync(image, "en")
        text = ""
        if isinstance(res, dict):
            text = res.get("text", "") or ""
        else:
            text = getattr(res, "text", "") or ""
        text = str(text).strip()
        if text:
            return text[:_OCR_MAX_CHARS]
        err = "[OCR_EMPTY] No text detected"
    except ImportError:
        err = "[OCR_UNAVAILABLE] pip install winocr"
    except Exception as e:
        err = f"[OCR_FAILED] winocr: {type(e).__name__}: {e}"

    try:
        import pytesseract
        text = pytesseract.image_to_string(image).strip()
        if text:
            return text[:_OCR_MAX_CHARS]
        if not err:
            err = "[OCR_EMPTY] No text detected"
    except ImportError:
        if not err:
            err = "[OCR_UNAVAILABLE] Install pytesseract + Tesseract engine"
    except Exception as e:
        if not err:
            err = f"[OCR_FAILED] tesseract: {type(e).__name__}: {e}"
    return err


def extract_ocr(image_bytes: bytes) -> str:
    """Best-effort local OCR; returns an explicit unavailable marker."""
    if not _PIL:
        return "[OCR_UNAVAILABLE] Pillow is not installed"
    try:
        return ocr_pil(PIL.Image.open(io.BytesIO(image_bytes)))
    except Exception as e:
        return f"[OCR_FAILED] {type(e).__name__}: {e}"


def _cv2_backend() -> int:
    """Return the best OpenCV camera backend for the current OS."""
    if not _CV2:
        return 0
    os_name = _get_os()
    if os_name == "windows":
        return cv2.CAP_DSHOW    
    if os_name == "mac":
        return cv2.CAP_AVFOUNDATION  
    return cv2.CAP_ANY


def _probe_camera(index: int, backend: int, warmup: int = 5) -> bool:

    if not _CV2:
        return False
    cap = cv2.VideoCapture(index, backend)
    if not cap.isOpened():
        cap.release()
        return False
    for _ in range(warmup):
        cap.read()
    ret, frame = cap.read()
    cap.release()
    if not ret or frame is None:
        return False
    return bool(np.mean(frame) > 8)


def _detect_camera_index() -> int:

    backend = _cv2_backend()
    print("[Vision] 🔍 Auto-detecting camera...")
    for idx in range(6):
        if _probe_camera(idx, backend):
            print(f"[Vision] ✅ Camera found at index {idx}")
            _save_config_key("camera_index", idx)
            return idx
        print(f"[Vision] ⚠️  Camera index {idx}: no usable frame")

    print("[Vision] ⚠️  No camera found — defaulting to index 0")
    _save_config_key("camera_index", 0)
    return 0


def _get_camera_index() -> int:
    cfg = _load_config()
    if "camera_index" in cfg:
        return int(cfg["camera_index"])
    return _detect_camera_index()


def _capture_camera() -> tuple[bytes, str]:
    if not _CV2:
        raise RuntimeError("OpenCV (cv2) is not installed. Run: pip install opencv-python")

    from actions.camera_source import CAMERA
    frame = CAMERA.snapshot()

    if _PIL:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img = PIL.Image.fromarray(rgb)
        img.thumbnail((_IMG_MAX_W, _IMG_MAX_H), PIL.Image.Resampling.BILINEAR)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=_JPEG_Q)
        return buf.getvalue(), "image/jpeg"

    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, _JPEG_Q])
    return buf.tobytes(), "image/jpeg"
