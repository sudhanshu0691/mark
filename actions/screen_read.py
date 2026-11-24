"""
screen_read  -  reliable "what is on my screen right now?" for Mark (and for checking its own work).

The old screen_process hands a picture to the live voice session on the NEXT turn, which is fragile
(interrupts, cool-downs and a stuck busy flag made Mark "blind").  This tool answers inside the tool call:

  mode=state   exact facts, no AI: foreground window title/exe, every open window, maximized/minimized
  mode=text    the real text on screen: UI-Automation text of the front window + OCR of a full-resolution screenshot
  mode=ask     screenshot -> Gemini vision -> a written answer to `question` (what is open? is X selected?)
  mode=verify  PASS/FAIL check after Mark did something:
                 expect_window = regex that must match the front window's title or exe
                 expect_text   = text (or list) that must be visible (UIA or OCR)
                 wait          = seconds to keep re-checking (slow apps)
"""
from __future__ import annotations

import io
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout
from pathlib import Path

from core.result import ActionResult, done, failed, unverified
try:
    from actions import win_manager as wm
except Exception:                                    # pragma: no cover
    wm = None

_CFG = Path(__file__).resolve().parent.parent / "config" / "api_keys.json"
VISION_MODEL = "role:vision"
_VISION_SYSTEM = (
    "You are the eyes of a Windows PC assistant. You get ONE screenshot. Answer the question about it precisely. "
    "Always say which application/window is in front. Quote visible text exactly. Mention what is selected/focused/"
    "highlighted if asked. If something is not visible or you are unsure, say so - never guess. "
    "Reply in the language of the question (Hinglish is fine), at most 6 short sentences."
)



def grab_full(monitor: int = 1, region: dict | None = None):
    """Full-resolution PIL image of the screen (needs mss + Pillow)."""
    import mss
    from PIL import Image
    with mss.MSS() as sct:
        mons = sct.monitors
        target = dict(mons[monitor if 0 <= monitor < len(mons) else 1])
        if region:
            for k in ("left", "top", "width", "height"):
                if k in region:
                    target[k] = int(region[k])
        shot = sct.grab(target)
        return Image.frombytes("RGB", shot.size, shot.rgb)


def _jpeg(img, max_w: int = 1600, q: int = 85) -> bytes:
    from PIL import Image
    im = img.copy()
    im.thumbnail((max_w, max_w * 9 // 16), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=q)
    return buf.getvalue()


def vision_answer(img_bytes: bytes, question: str, mime: str = "image/jpeg", extra: str = "") -> str:
    """Synchronous Gemini vision call; returns plain text or raises."""
    from core import llm as genai  # configured LLM client (drop-in)
    from core.llm import types as gt
    client = genai.Client()
    q = question.strip() or "What is on the screen? Which window is in front?"
    if extra:
        q += "\n\nFACTS FROM WINDOWS (exact):\n" + extra
    resp = client.models.generate_content(
        model=VISION_MODEL,
        contents=[gt.Part.from_bytes(data=img_bytes, mime_type=mime), q],
        config=gt.GenerateContentConfig(system_instruction=_VISION_SYSTEM, temperature=0.1),
    )
    answer = (resp.text or "").strip()
    if answer:
        return answer
    return answer


# ── facts without AI ─────────────────────────────────────────────────────────
def _facts() -> str:
    if wm is None or not wm._WIN:
        return ""
    a = wm.active_window()
    ws = wm.list_windows()
    lines = [f"FRONT: {a['title'][:90]} [{a['exe']}]{' maximized' if a['maximized'] else ''}" if a else "FRONT: (none)"]
    for w in ws[:14]:
        flag = "min" if w["minimized"] else ("max" if w["maximized"] else "")
        lines.append(f"- {w['title'][:70]} [{w['exe']}] {flag}".rstrip())
    return "\n".join(lines)


def _uia_text(hwnd: int, limit: int = 250, budget: float = 6.0) -> list[str]:
    """Text of the front window through UI Automation (fast for native apps, partial for browsers)."""
    def work() -> list[str]:
        from pywinauto import Desktop
        win = Desktop(backend="uia").window(handle=hwnd)
        out: list[str] = []
        for ctype in ("Text", "Edit", "TabItem", "Button", "ListItem", "Hyperlink", "MenuItem"):
            try:
                for c in win.descendants(control_type=ctype):
                    t = (c.window_text() or "").strip()
                    if t and t not in out:
                        out.append(t if ctype == "Text" else f"[{ctype}] {t}")
                    if len(out) >= limit:
                        return out
            except Exception:
                continue
        return out
    ex = ThreadPoolExecutor(max_workers=1)
    try:
        return ex.submit(work).result(timeout=budget)
    except (FutTimeout, Exception):
        return []
    finally:
        ex.shutdown(wait=False, cancel_futures=True)


def _ocr_text(monitor: int = 1, region: dict | None = None) -> str:
    try:
        from actions.screen_processor import ocr_pil
        return ocr_pil(grab_full(monitor, region))
    except Exception as e:
        return f"[OCR_FAILED] {type(e).__name__}: {e}"


def _visible_text(hwnd: int | None) -> tuple[str, str]:
    """(combined text, which sources worked)"""
    parts, src = [], []
    if hwnd:
        u = _uia_text(hwnd)
        if u:
            parts.append("\n".join(u)); src.append("UIA")
    o = _ocr_text()
    if not o.startswith("[OCR_"):
        parts.append(o); src.append("OCR")
    return "\n".join(parts), "+".join(src) or "none"


# ── the tool ─────────────────────────────────────────────────────────────────
def screen_read(args: dict, player=None) -> ActionResult | str:
    mode = str(args.get("mode", "state")).lower().strip()
    if mode == "":
        mode = "state"
    a = wm.active_window() if (wm and wm._WIN) else None
    hwnd = a["hwnd"] if a else None

    if mode == "state":
        f = _facts()
        return done("Current windows read from Windows (exact)", evidence=f) if f else \
            failed("Cannot list windows", "window facts need Windows")

    if mode == "text":
        text, src = _visible_text(hwnd)
        if not text.strip():
            return unverified("Could not read any text from the screen",
                              reason="UI Automation returned nothing and OCR is unavailable (pip install winocr  or install Tesseract). "
                                     "Use mode=ask for a vision description instead.", evidence=_facts())
        return done(f"Screen text read via {src}", evidence=_facts() + "\n--- TEXT ---\n" + text[:3500])

    if mode == "ask":
        q = str(args.get("question") or args.get("text") or "")
        try:
            img = grab_full(int(args.get("monitor", 1)), args.get("region"))
            ans = vision_answer(_jpeg(img), q, extra=_facts())
        except Exception as e:
            return failed("Could not look at the screen", f"{type(e).__name__}: {e}")
        if not ans:
            return unverified("Vision returned an empty answer", reason="try again or use mode=text", evidence=_facts())
        return done("Looked at the screen", evidence=f"ANSWER: {ans}\n{_facts()}")

    if mode == "verify":
        exp_w = str(args.get("expect_window") or "").strip()
        exp_t = args.get("expect_text") or []
        exp_t = [exp_t] if isinstance(exp_t, str) else list(exp_t)
        exp_t = [t for t in (str(x).strip() for x in exp_t) if t]
        if not exp_w and not exp_t:
            return failed("verify needs expect_window and/or expect_text", "nothing to check")
        end = time.time() + max(0.0, min(float(args.get("wait", 0) or 0), 60.0))
        missing: list[str] = []
        seen_text, src = "", "none"
        while True:
            missing = []
            a = wm.active_window() if (wm and wm._WIN) else None
            if exp_w:
                hay = f"{a['title']} {a['exe']}" if a else ""
                if not re.search(exp_w, hay, re.I):
                    missing.append(f"front window does not match /{exp_w}/ (front is: {a['title'][:60] if a else 'unknown'} [{a['exe'] if a else '?'}])")
            if exp_t:
                seen_text, src = _visible_text(a["hwnd"] if a else None)
                low = seen_text.lower()
                for t in exp_t:
                    if t.lower() not in low:
                        missing.append(f"text not visible: '{t}'")
            if not missing or time.time() >= end:
                break
            time.sleep(0.8)
        ev = _facts() + (f"\n(text sources: {src})" if exp_t else "")
        if not missing:
            return done("Verified on screen: " + "; ".join(([f"window /{exp_w}/"] if exp_w else []) + [f"'{t}'" for t in exp_t]), evidence=ev)
        if exp_t and src == "none":
            return unverified("Could not verify - no way to read screen text", reason="; ".join(missing) + " | install winocr/Tesseract or use mode=ask", evidence=ev)
        return failed("Screen does NOT show what was expected", "; ".join(missing))

    return failed(f"Unknown screen_read mode '{mode}'", "valid: state | text | ask | verify")
