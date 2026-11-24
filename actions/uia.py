"""
Small UI-Automation helpers (Windows).  Lets Mark find buttons / text in real
app windows instead of clicking blindly.  Everything degrades into a clear
failure reason when pywinauto or the window is missing.
"""
from __future__ import annotations

import platform
import re
import time

_WIN = platform.system() == "Windows"

try:
    from pywinauto import Desktop
    _HAVE = True
except Exception:  # pragma: no cover
    Desktop = None  # type: ignore
    _HAVE = False


def available() -> tuple[bool, str]:
    if not _WIN:
        return False, "UI Automation works only on Windows"
    if not _HAVE:
        return False, "pywinauto is not installed (pip install pywinauto)"
    return True, ""


def windows(title_re: str | None = None, class_re: str | None = None):
    ok, _ = available()
    if not ok:
        return []
    out = []
    for w in Desktop(backend="uia").windows():
        try:
            t = w.window_text() or ""
            c = w.element_info.class_name or ""
            if title_re and not re.search(title_re, t, re.I):
                continue
            if class_re and not re.search(class_re, c, re.I):
                continue
            out.append(w)
        except Exception:
            continue
    return out


def find_window(title_re: str, timeout: float = 0.0):
    end = time.time() + timeout
    while True:
        ws = windows(title_re)
        if ws:
            return ws[0]
        if time.time() >= end:
            return None
        time.sleep(0.5)


def focus(win) -> bool:
    try:
        if win.is_minimized():
            win.restore()
        win.set_focus()
        return True
    except Exception:
        return False


def buttons_matching(win, name_re: str):
    found = []
    try:
        for b in win.descendants(control_type="Button"):
            try:
                n = b.window_text() or b.element_info.name or ""
                if re.search(name_re, n, re.I):
                    found.append((n, b))
            except Exception:
                continue
    except Exception:
        pass
    return found


def texts(win, limit: int = 400) -> list[str]:
    out: list[str] = []
    try:
        for t in win.descendants(control_type="Text"):
            try:
                s = (t.window_text() or "").strip()
                if s:
                    out.append(s)
            except Exception:
                continue
            if len(out) >= limit:
                break
    except Exception:
        pass
    return out


def toast_windows():
    """Windows 10/11 toast / notification host windows."""
    return windows(class_re=r"CoreWindow|Toast|NotificationHost|XamlExplorerHost|ControlCenter") + \
        windows(title_re=r"new notification|notification")
