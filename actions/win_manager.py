"""
Window / app manager  (Windows).  One place that knows what is RUNNING, what is PINNED on the
taskbar, how to START something and how to PROVE it worked.

Rules this module implements
  1. An app that already has a window (= it is on the taskbar) is NEVER searched / launched again:
     we restore it, bring it to the front and maximize it.  Browsers get a fresh tab (Ctrl+T) instead of
     a second window; a URL goes into a new tab of the running browser.
  2. An app that is not running is started by the most direct way available:
        taskbar-pinned shortcut -> exe on PATH / App Paths registry -> Start-menu shortcut
        -> UWP app (Get-StartApps) -> URI scheme -> typing into Start search (last resort)
     then we WAIT for its window, focus + maximize it.
  3. Nothing is reported as done unless the foreground window afterwards is really that app
     (title, exe and maximized state are returned as evidence).
"""
from __future__ import annotations

import difflib
import json
import os
import platform
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from core.result import ActionResult, done, failed, unverified

_WIN = platform.system() == "Windows"
_HERE = Path(__file__).resolve().parent.parent
_CACHE = _HERE / "memory" / "app_index.json"

# ──────────────────────────────────────────────────────────────────────────────
# Win32 plumbing
# ──────────────────────────────────────────────────────────────────────────────
if _WIN:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    try:
        dwmapi = ctypes.WinDLL("dwmapi")
    except OSError:  # pragma: no cover
        dwmapi = None

    _ENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [_ENUMPROC, wintypes.LPARAM]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsZoomed.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.SwitchToThisWindow.argtypes = [wintypes.HWND, wintypes.BOOL]
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.IsWindow.argtypes = [wintypes.HWND]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                    ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

_SW_RESTORE, _SW_MAXIMIZE, _SW_MINIMIZE, _SW_SHOW = 9, 3, 6, 5
_WS_EX_TOOLWINDOW, _WS_EX_APPWINDOW = 0x80, 0x40000
_SKIP_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Button", "tooltips_class32"}
_SKIP_TITLES = {"program manager", "windows input experience", "microsoft text input application", "settings "}


def _hwnd_int(h) -> int:
    return int(h) if h else 0


def _exe_of_pid(pid: int) -> str:
    if not _WIN or not pid:
        return ""
    h = kernel32.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        n = wintypes.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            return os.path.basename(buf.value).lower()
    finally:
        kernel32.CloseHandle(h)
    return ""


def list_windows() -> list[dict]:
    """Real top-level app windows (what you see on the taskbar), topmost first."""
    if not _WIN:
        return []
    out: list[dict] = []

    def cb(hwnd, _):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            n = user32.GetWindowTextLengthW(hwnd)
            if n <= 0:
                return True
            tb = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, tb, n + 1)
            title = tb.value.strip()
            if not title or title.lower() in _SKIP_TITLES:
                return True
            cb_ = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cb_, 256)
            cls = cb_.value
            if cls in _SKIP_CLASSES:
                return True
            ex = user32.GetWindowLongW(hwnd, -20)
            if (ex & _WS_EX_TOOLWINDOW) and not (ex & _WS_EX_APPWINDOW):
                return True
            if user32.GetWindow(hwnd, 4) and not (ex & _WS_EX_APPWINDOW):          # GW_OWNER
                return True
            if dwmapi is not None:                                                  # cloaked (hidden UWP / other desktop)
                c = ctypes.c_int(0)
                if dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(c), 4) == 0 and c.value:
                    return True
            pid = wintypes.DWORD(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            out.append({"hwnd": _hwnd_int(hwnd), "title": title, "cls": cls, "pid": int(pid.value),
                        "exe": _exe_of_pid(int(pid.value)), "minimized": bool(user32.IsIconic(hwnd)),
                        "maximized": bool(user32.IsZoomed(hwnd))})
        except Exception:
            pass
        return True

    user32.EnumWindows(_ENUMPROC(cb), 0)
    return out


def active_window() -> dict | None:
    if not _WIN:
        return None
    h = _hwnd_int(user32.GetForegroundWindow())
    if not h:
        return None
    for w in list_windows():
        if w["hwnd"] == h:
            return w
    n = user32.GetWindowTextLengthW(h)
    tb = ctypes.create_unicode_buffer(max(n, 0) + 1)
    user32.GetWindowTextW(h, tb, n + 1)
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
    return {"hwnd": h, "title": tb.value, "cls": "", "pid": int(pid.value), "exe": _exe_of_pid(int(pid.value)),
            "minimized": False, "maximized": bool(user32.IsZoomed(h))}


def _fg() -> int:
    return _hwnd_int(user32.GetForegroundWindow()) if _WIN else 0


def _alive(hwnd: int) -> bool:
    return bool(_WIN and user32.IsWindow(hwnd))


def bring_to_front(hwnd: int, maximize: bool = True) -> tuple[bool, bool]:
    """Restore + foreground (+ maximize).  Returns (is_foreground, is_maximized)."""
    if not _WIN or not _alive(hwnd):
        return False, False
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, _SW_RESTORE)
        time.sleep(0.15)
    if maximize and not user32.IsZoomed(hwnd):
        user32.ShowWindow(hwnd, _SW_MAXIMIZE)
        time.sleep(0.1)

    def attempt(kind: int) -> bool:
        if kind == 0:                                           # attach to the foreground thread's input queue
            fg = user32.GetForegroundWindow()
            fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
            cur = kernel32.GetCurrentThreadId()
            if fg_tid and fg_tid != cur:
                user32.AttachThreadInput(cur, fg_tid, True)
            try:
                user32.BringWindowToTop(hwnd)
                user32.SetForegroundWindow(hwnd)
            finally:
                if fg_tid and fg_tid != cur:
                    user32.AttachThreadInput(cur, fg_tid, False)
        elif kind == 1:
            user32.SwitchToThisWindow(hwnd, True)
        else:                                                   # tap Alt: lifts Windows' foreground lock
            user32.keybd_event(0x12, 0, 0, 0)
            user32.keybd_event(0x12, 0, 2, 0)
            user32.SetForegroundWindow(hwnd)
        time.sleep(0.2)
        return _fg() == hwnd

    ok = any(attempt(k) for k in (0, 1, 2))
    return ok, bool(user32.IsZoomed(hwnd))


def close_window(hwnd: int) -> bool:
    if not _WIN:
        return False
    user32.PostMessageW(hwnd, 0x0010, 0, 0)                      # WM_CLOSE
    for _ in range(10):
        time.sleep(0.2)
        if not _alive(hwnd):
            return True
    return not _alive(hwnd)


def minimize_window(hwnd: int) -> bool:
    if not _WIN:
        return False
    user32.ShowWindow(hwnd, _SW_MINIMIZE)
    time.sleep(0.2)
    return bool(user32.IsIconic(hwnd))


# ──────────────────────────────────────────────────────────────────────────────
# app knowledge
# ──────────────────────────────────────────────────────────────────────────────
@dataclass
class AppSpec:
    key: str
    display: str
    names: list[str] = field(default_factory=list)       # alternative spoken names
    exes: list[str] = field(default_factory=list)        # process names of its windows
    titles: list[str] = field(default_factory=list)      # title keywords (only trusted for UWP host / exact exe)
    classes: list[str] = field(default_factory=list)
    uwp: bool = False                                    # window lives in ApplicationFrameHost.exe
    browser: bool = False
    newtab_keys: tuple = ()                              # hotkey that opens a new tab/session
    url: str = ""                                        # for website shortcuts


_APPS: list[AppSpec] = [
    AppSpec("chrome", "Google Chrome", ["chrome", "google chrome"], ["chrome.exe"], browser=True, newtab_keys=("ctrl", "t")),
    AppSpec("edge", "Microsoft Edge", ["edge", "microsoft edge", "msedge"], ["msedge.exe"], browser=True, newtab_keys=("ctrl", "t")),
    AppSpec("firefox", "Firefox", ["firefox", "mozilla firefox"], ["firefox.exe"], browser=True, newtab_keys=("ctrl", "t")),
    AppSpec("brave", "Brave", ["brave", "brave browser"], ["brave.exe"], browser=True, newtab_keys=("ctrl", "t")),
    AppSpec("opera", "Opera", ["opera", "opera gx", "operagx"], ["opera.exe"], browser=True, newtab_keys=("ctrl", "t")),
    AppSpec("vivaldi", "Vivaldi", ["vivaldi"], ["vivaldi.exe"], browser=True, newtab_keys=("ctrl", "t")),
    AppSpec("vscode", "Visual Studio Code", ["vscode", "vs code", "visual studio code", "code"], ["code.exe"]),
    AppSpec("whatsapp", "WhatsApp", ["whatsapp", "whats app"], ["whatsapp.exe", "whatsapp.root.exe"], ["whatsapp"], uwp=True),
    AppSpec("telegram", "Telegram", ["telegram"], ["telegram.exe"]),
    AppSpec("discord", "Discord", ["discord"], ["discord.exe"]),
    AppSpec("slack", "Slack", ["slack"], ["slack.exe"]),
    AppSpec("zoom", "Zoom", ["zoom"], ["zoom.exe"]),
    AppSpec("teams", "Microsoft Teams", ["teams", "microsoft teams", "msteams"], ["ms-teams.exe", "teams.exe", "msteams.exe"]),
    AppSpec("skype", "Skype", ["skype"], ["skype.exe"]),
    AppSpec("signal", "Signal", ["signal"], ["signal.exe"]),
    AppSpec("spotify", "Spotify", ["spotify"], ["spotify.exe"]),
    AppSpec("vlc", "VLC", ["vlc", "vlc media player"], ["vlc.exe"]),
    AppSpec("notepad", "Notepad", ["notepad", "text editor"], ["notepad.exe"]),
    AppSpec("calculator", "Calculator", ["calculator", "calc"], ["calculatorapp.exe", "calc.exe"], ["calculator"], uwp=True),
    AppSpec("explorer", "File Explorer", ["file explorer", "explorer", "files", "this pc", "my computer"], ["explorer.exe"],
            classes=["CabinetWClass"], newtab_keys=("ctrl", "t")),
    AppSpec("settings", "Settings", ["settings", "windows settings", "pc settings"], ["systemsettings.exe"], ["settings"], uwp=True),
    AppSpec("taskmgr", "Task Manager", ["task manager", "taskmgr"], ["taskmgr.exe"]),
    AppSpec("terminal", "Windows Terminal", ["terminal", "windows terminal", "wt"], ["windowsterminal.exe", "wt.exe"],
            newtab_keys=("ctrl", "shift", "t")),
    AppSpec("cmd", "Command Prompt", ["cmd", "command prompt"], ["cmd.exe"]),
    AppSpec("powershell", "PowerShell", ["powershell", "pwsh"], ["powershell.exe", "pwsh.exe"]),
    AppSpec("word", "Microsoft Word", ["word", "microsoft word", "ms word"], ["winword.exe"]),
    AppSpec("excel", "Microsoft Excel", ["excel", "microsoft excel", "ms excel"], ["excel.exe"]),
    AppSpec("powerpoint", "PowerPoint", ["powerpoint", "ppt", "microsoft powerpoint"], ["powerpnt.exe"]),
    AppSpec("outlook", "Outlook", ["outlook"], ["outlook.exe", "olk.exe"]),
    AppSpec("paint", "Paint", ["paint", "mspaint"], ["mspaint.exe"]),
    AppSpec("steam", "Steam", ["steam"], ["steam.exe", "steamwebhelper.exe"]),
    AppSpec("epic", "Epic Games Launcher", ["epic", "epic games", "epic games launcher"], ["epicgameslauncher.exe"]),
    AppSpec("obsidian", "Obsidian", ["obsidian"], ["obsidian.exe"]),
    AppSpec("notion", "Notion", ["notion"], ["notion.exe"]),
    AppSpec("figma", "Figma", ["figma"], ["figma.exe"]),
    AppSpec("postman", "Postman", ["postman"], ["postman.exe"]),
    AppSpec("blender", "Blender", ["blender"], ["blender.exe"]),
    AppSpec("capcut", "CapCut", ["capcut"], ["capcut.exe"]),
    AppSpec("photos", "Photos", ["photos"], ["photos.exe", "microsoft.photos.exe"], ["photos"], uwp=True),
    AppSpec("camera", "Camera", ["camera"], ["windowscamera.exe"], ["camera"], uwp=True),
    AppSpec("clock", "Clock", ["clock", "alarms"], ["time.exe"], ["clock"], uwp=True),
]
_WEB = {
    "youtube": "https://www.youtube.com", "gmail": "https://mail.google.com", "instagram": "https://www.instagram.com",
    "netflix": "https://www.netflix.com", "tiktok": "https://www.tiktok.com", "github": "https://github.com",
    "facebook": "https://www.facebook.com", "twitter": "https://x.com", "x": "https://x.com",
    "linkedin": "https://www.linkedin.com", "chatgpt": "https://chatgpt.com", "google": "https://www.google.com",
    "maps": "https://maps.google.com", "google maps": "https://maps.google.com", "drive": "https://drive.google.com",
    "google drive": "https://drive.google.com", "amazon": "https://www.amazon.in", "flipkart": "https://www.flipkart.com",
    "reddit": "https://www.reddit.com", "whatsapp web": "https://web.whatsapp.com",
}
_URI = {"settings": "ms-settings:", "calculator": "calculator:", "camera": "microsoft.windows.camera:",
        "photos": "ms-photos:", "clock": "ms-clock:", "store": "ms-windows-store:", "microsoft store": "ms-windows-store:"}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def resolve(name: str) -> AppSpec:
    """Spoken name -> AppSpec (known app, website, or a generic guess)."""
    raw = (name or "").strip()
    low = raw.lower().strip()
    n = _norm(low)
    for a in _APPS:
        if low in a.names or n in {_norm(x) for x in a.names} or n == _norm(a.key):
            return a
    if low in _WEB:
        return AppSpec(low, raw, [low], url=_WEB[low])
    if re.match(r"^(https?://|www\.)", low) or re.match(r"^[\w-]+(\.[\w-]+)+(/.*)?$", low):
        return AppSpec(low, raw, [low], url=low if "://" in low else "https://" + low)
    # loose containment ("open google chrome please")
    for a in _APPS:
        for x in a.names:
            if len(_norm(x)) >= 4 and _norm(x) in n:
                return a
    stem = raw.lower().replace(".exe", "")
    return AppSpec(_norm(stem) or low, raw, [stem], list(dict.fromkeys([_norm(stem) + ".exe", stem.strip() + ".exe"])))


def _window_matches(w: dict, spec: AppSpec) -> bool:
    exe = w["exe"]
    stem = exe[:-4] if exe.endswith(".exe") else exe
    if exe in spec.exes:
        if spec.classes and exe == "explorer.exe":
            return w["cls"] in spec.classes
        return True
    keys = {_norm(x) for x in spec.names} | {_norm(spec.key)}
    if stem and len(_norm(stem)) >= 3 and _norm(stem) in keys:
        return True
    if exe == "applicationframehost.exe" or spec.uwp:
        t = w["title"].lower()
        if any(k in t for k in spec.titles):
            return exe in ("applicationframehost.exe",) or exe in spec.exes
    if w["cls"] in spec.classes and spec.classes:
        return True
    return False


def find_windows(spec: AppSpec) -> list[dict]:
    return [w for w in list_windows() if _window_matches(w, spec)]


# ──────────────────────────────────────────────────────────────────────────────
# launching
# ──────────────────────────────────────────────────────────────────────────────
def _taskbar_pins() -> list[Path]:
    d = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Internet Explorer" / "Quick Launch" / "User Pinned" / "TaskBar"
    return sorted(d.glob("*.lnk")) if d.exists() else []


def _start_menu_links() -> list[Path]:
    roots = [Path(os.environ.get("ProgramData", "C:/ProgramData")) / "Microsoft/Windows/Start Menu/Programs",
             Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs"]
    out: list[Path] = []
    for r in roots:
        if r.exists():
            out += list(r.rglob("*.lnk"))
    return out


def _score(query: str, candidate: str) -> float:
    q, c = _norm(query), _norm(candidate)
    if not q or not c:
        return 0.0
    if q == c:
        return 1.0
    if q in c or c in q:
        return 0.85 + 0.1 * (min(len(q), len(c)) / max(len(q), len(c)))
    return difflib.SequenceMatcher(None, q, c).ratio() * 0.9


def _best_path(query, paths: list[Path], threshold: float = 0.72) -> Path | None:
    """query may be one name or a list of alternative names; best score over all of them wins."""
    qs = [query] if isinstance(query, str) else list(query)
    best, bs = None, 0.0
    for p in paths:
        s = max((_score(q, p.stem) for q in qs), default=0.0)
        if s > bs:
            best, bs = p, s
    return best if bs >= threshold else None


def _startapps() -> list[dict]:
    """UWP + desktop apps known to Windows: [{'Name':..,'AppID':..}] (cached for a day)."""
    try:
        if _CACHE.exists() and time.time() - _CACHE.stat().st_mtime < 86400:
            return json.loads(_CACHE.read_text(encoding="utf-8"))
    except Exception:
        pass
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                            "Get-StartApps | ConvertTo-Json -Compress"], capture_output=True, text=True, timeout=25)
        data = json.loads(r.stdout or "[]")
        data = [data] if isinstance(data, dict) else data
        _CACHE.parent.mkdir(exist_ok=True)
        _CACHE.write_text(json.dumps(data), encoding="utf-8")
        return data
    except Exception:
        return []


def _app_path_exe(exe: str) -> str | None:
    found = shutil.which(exe)
    if found:
        return found
    try:
        import winreg
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(hive, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}") as k:
                    val, _ = winreg.QueryValueEx(k, "")
                    if val and os.path.exists(val.strip('"')):
                        return val.strip('"')
            except OSError:
                continue
    except Exception:
        pass
    return None


def default_browser_spec() -> AppSpec:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice") as k:
            prog = winreg.QueryValueEx(k, "ProgId")[0].lower()
        for needle, key in (("chrome", "chrome"), ("msedge", "edge"), ("edgehtm", "edge"), ("firefox", "firefox"),
                            ("brave", "brave"), ("opera", "opera"), ("vivaldi", "vivaldi")):
            if needle in prog:
                return resolve(key)
    except Exception:
        pass
    return resolve("edge")


def _run_detached(args: list[str] | str, shell: bool = False) -> None:
    subprocess.Popen(args, shell=shell, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)


def launch(spec: AppSpec, url: str | None = None, query: str = "") -> str | None:
    """Start the app. Returns a short description of HOW it was started, or None."""
    q = query or spec.display
    qs = list(dict.fromkeys([x for x in (spec.display, q, *spec.names) if x]))
    # --- browsers / websites: the exe with a URL opens a NEW TAB in the running window
    if spec.browser or (spec.url and not spec.exes):
        b = spec if spec.browser else default_browser_spec()
        target = url or spec.url
        if target:
            from urllib.parse import urlsplit
            address = urlsplit(target)
            if address.scheme.lower() not in {"http", "https"} or not address.hostname:
                return None
        exe = next((p for p in (_app_path_exe(e) for e in b.exes) if p), None)
        if exe:
            args = [exe]
            if target:
                args += (["-new-tab", target] if b.key == "firefox" else [target])
            _run_detached(args)
            return f"{b.display} (exe){' -> ' + target if target else ''}"
        if target:
            os.startfile(target)                               # type: ignore[attr-defined]
            return f"default browser -> {target}"
    # --- 1. taskbar-pinned shortcut
    pin = _best_path(qs, _taskbar_pins(), 0.8)
    if pin:
        os.startfile(str(pin))                                 # type: ignore[attr-defined]
        return f"taskbar pin '{pin.stem}'"
    # --- 2. known exe on PATH / App Paths
    for e in spec.exes:
        p = _app_path_exe(e)
        if p:
            _run_detached([p]); return f"exe {os.path.basename(p)}"
    # --- 3. URI scheme (Settings, Calculator, Store ...)
    uri = _URI.get(spec.key) or _URI.get(q.lower())
    if uri:
        os.startfile(uri)                                      # type: ignore[attr-defined]
        return f"uri {uri}"
    # --- 4. Start-menu shortcut
    lnk = _best_path(qs, _start_menu_links())
    if lnk:
        os.startfile(str(lnk))                                 # type: ignore[attr-defined]
        return f"start menu '{lnk.stem}'"
    # --- 5. UWP / Store apps
    apps = _startapps()
    best, bs = None, 0.0
    for a in apps:
        s = max(_score(x, str(a.get("Name", ""))) for x in qs)
        if s > bs:
            best, bs = a, s
    if best and bs >= 0.72:
        _run_detached(["explorer.exe", f"shell:AppsFolder\\{best['AppID']}"])
        return f"store/start app '{best['Name']}'"
    # --- 6. last resort: type into Start search
    try:
        import pyautogui
        pyautogui.press("win"); time.sleep(0.7)
        pyautogui.write(q, interval=0.04); time.sleep(0.9)
        pyautogui.press("enter")
        return "start-menu search (typed)"
    except Exception:
        return None


def wait_for_window(spec: AppSpec, before: set[int], timeout: float = 12.0) -> dict | None:
    end = time.time() + timeout
    while time.time() < end:
        ws = find_windows(spec)
        new = [w for w in ws if w["hwnd"] not in before]
        if new:
            return new[0]
        if ws and time.time() > end - timeout * 0.4:           # app was running hidden (tray) and just got a window
            return ws[0]
        time.sleep(0.35)
    return None


def _describe(w: dict | None) -> str:
    if not w:
        return "no window"
    return f"'{w['title'][:70]}' ({w['exe'] or '?'})"


# ──────────────────────────────────────────────────────────────────────────────
# high level: open-or-focus (used by open_app) and the window_manager tool
# ──────────────────────────────────────────────────────────────────────────────
def _log(player, text: str):
    print(f"[win] {text}")
    if player is not None and hasattr(player, "write_log"):
        try:
            player.write_log(f"[win] {text}")
        except Exception:
            pass


def _hotkey(*keys):
    import pyautogui
    pyautogui.hotkey(*keys)


def open_or_focus(app_name: str, url: str | None = None, new_tab: bool | None = None, maximize: bool = True,
                  player=None) -> ActionResult:
    if not _WIN:
        return failed("open_or_focus is Windows-only", "this build of the window manager needs Windows")
    spec = resolve(app_name)
    url = (url or "").strip() or None
    if new_tab is None:
        new_tab = spec.browser
    target_spec = spec
    if spec.url and not spec.exes and not spec.browser:          # website name -> use default browser
        url = url or spec.url
        target_spec = default_browser_spec()
    wins = find_windows(target_spec)
    evidence: list[str] = []

    if wins:                                                       # ── already running: do NOT search / relaunch
        w = wins[0]
        _log(player, f"{target_spec.display} already open -> focusing")
        if (target_spec.browser) and url:
            how = launch(target_spec, url)                         # new tab in the running window
            evidence.append(f"opened {url} as new tab via {how}")
            time.sleep(0.9)
            wins = find_windows(target_spec) or wins
            w = wins[0]
        fg_ok, zoomed = bring_to_front(w["hwnd"], maximize)
        if new_tab and not url and target_spec.newtab_keys and fg_ok:
            try:
                time.sleep(0.15)
                _hotkey(*target_spec.newtab_keys)
                evidence.append("new tab opened (hotkey)")
                time.sleep(0.4)
            except Exception as e:
                evidence.append(f"new tab hotkey failed: {e}")
        return _verdict(target_spec, w["hwnd"], maximize, f"{target_spec.display} was already running", evidence)

    # ── not running: launch, wait for the window, focus
    before = {w["hwnd"] for w in list_windows()}
    how = launch(target_spec, url, query=app_name)
    if not how:
        return failed(f"Could not start {app_name}",
                      "no taskbar pin, exe, Start-menu entry or Store app matched that name - is it installed? "
                      "(try software_manager install)")
    _log(player, f"launched {app_name} via {how}")
    evidence.append(f"started via {how}")
    w = wait_for_window(target_spec, before, 14.0 if target_spec.browser else 12.0)
    if not w:
        return unverified(f"Started {app_name} but no window appeared",
                          reason=f"waited 12s after {how}; it may be loading, minimised to tray, or the name matched a different app",
                          evidence="; ".join(evidence))
    bring_to_front(w["hwnd"], maximize)
    return _verdict(target_spec, w["hwnd"], maximize, f"Opened {target_spec.display}", evidence)


def _verdict(spec: AppSpec, hwnd: int, maximize: bool, head: str, evidence: list[str]) -> ActionResult:
    time.sleep(0.25)
    a = active_window()
    fg_ok = bool(a and a["hwnd"] == hwnd) or bool(a and _window_matches(a, spec))
    zoomed = bool(a and a["maximized"])
    evidence.append(f"foreground={_describe(a)}")
    evidence.append(f"maximized={zoomed}")
    ev = "; ".join(evidence)
    if fg_ok and (zoomed or not maximize):
        return done(f"{head} and it is now in front" + (" (maximized)" if zoomed else ""), evidence=ev)
    if fg_ok:
        return done(f"{head} and it is in front (this window cannot be maximized / is fixed size)", evidence=ev)
    return unverified(f"{head}, but could not confirm it is in front",
                      reason="Windows blocked the focus change or another window took focus; foreground is "
                             + _describe(a), evidence=ev)


# ---- the `window_manager` tool ---------------------------------------------------
def _pick(args: dict) -> tuple[AppSpec | None, dict | None, str]:
    name = str(args.get("app") or args.get("name") or args.get("title") or "").strip()
    if not name:
        a = active_window()
        return None, a, "active window"
    spec = resolve(name)
    ws = find_windows(spec)
    if not ws:                                                       # fall back to a plain title search
        ws = [w for w in list_windows() if name.lower() in w["title"].lower()]
    return spec, (ws[0] if ws else None), name


def window_manager(args: dict, player=None) -> ActionResult | str:
    if not _WIN:
        return failed("window_manager needs Windows", "not running on Windows")
    act = str(args.get("action", "list")).lower().strip()

    if act in ("list", "running", "taskbar"):
        ws = list_windows()
        pins = [p.stem for p in _taskbar_pins()]
        lines = [f"{i+1}. {w['title'][:60]} [{w['exe'] or '?'}]{' (minimized)' if w['minimized'] else ''}{' (maximized)' if w['maximized'] else ''}"
                 for i, w in enumerate(ws)]
        a = active_window()
        return done(f"{len(ws)} windows open. Active: {_describe(a)}",
                    evidence="\n".join(lines) + ("\nPinned on taskbar: " + ", ".join(pins) if pins else ""))
    if act == "active":
        a = active_window()
        return done(f"Active window is {_describe(a)}", evidence=json.dumps(a or {}))

    if act == "wait":
        pat = str(args.get("title") or args.get("app") or "")
        end = time.time() + float(args.get("timeout", 15))
        while time.time() < end:
            for w in list_windows():
                if re.search(pat, w["title"], re.I) or pat.lower() in w["exe"]:
                    return done(f"Window appeared: {_describe(w)}")
            time.sleep(0.4)
        return failed(f"No window matching '{pat}' appeared", f"waited {args.get('timeout', 15)}s")

    spec, w, label = _pick(args)
    if act in ("focus", "switch", "show", "open"):
        if args.get("app") or args.get("name"):
            return open_or_focus(str(args.get("app") or args.get("name")), args.get("url"), args.get("new_tab"),
                                 bool(args.get("maximize", True)), player)
    if not w:
        return failed(f"No window found for '{label}'", "it is not open (use open_app to start it)")
    h = w["hwnd"]
    if act in ("maximize", "restore", "focus", "switch", "show"):
        bring_to_front(h, act in ("maximize", "focus", "switch", "show"))
        if act == "restore":
            user32.ShowWindow(h, _SW_RESTORE)
        a = active_window()
        ev = f"foreground={_describe(a)}; maximized={bool(a and a['maximized'])}"
        if a and a["hwnd"] == h:
            return done(f"{act}: {_describe(w)}", evidence=ev)
        return unverified(f"{act}: {_describe(w)} not confirmed in front", reason="Windows blocked the focus change", evidence=ev)
    if act == "minimize":
        return done(f"Minimized {_describe(w)}") if minimize_window(h) else unverified("Minimize not confirmed")
    if act == "close":
        return done(f"Closed {_describe(w)}") if close_window(h) else unverified(
            f"Sent close to {_describe(w)} but it is still open", reason="the app may be asking to save first")
    if act in ("snap_left", "snap_right", "snap_up", "snap_down"):
        bring_to_front(h, False)
        import pyautogui
        pyautogui.hotkey("win", act.split("_")[1])
        return unverified(f"Sent Win+{act.split('_')[1]} to {_describe(w)}", reason="snap position cannot be read back")
    return failed(f"Unknown window action '{act}'", "valid: list active focus maximize minimize restore close wait snap_left snap_right")
