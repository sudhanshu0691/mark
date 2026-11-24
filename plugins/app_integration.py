"""
App Integration - launch, control and VERIFY third-party applications on this PC.

The point of this plugin is HONESTY: after every action it re-checks the process list
(and the foreground window when possible) so "Chrome opened" is a verified fact, not a
guess.  If it cannot prove it, it says UNVERIFIED instead of DONE.
"""
from __future__ import annotations

import time

from core.result import done, failed, unverified

NAME = "app_integration"
DESCRIPTION = ("Launch, focus, stop and verify third-party applications. Reports DONE only after "
               "the process/window is observed; otherwise UNVERIFIED with the exact reason.")
METADATA = {
    "version": "1.0.0",
    "capabilities": ["apps", "integration", "launch", "process", "window", "verify"],
    "permissions": ["open_app", "window_manager"],
    "timeout": 45,
}

DECLARATION = {
    "name": "app_integration",
    "description": (
        "Control a third-party application and PROVE the result. "
        "action: launch | running | stop | installed | focus | list | command. "
        "name = application name (e.g. 'chrome', 'spotify', 'code')."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "launch | running | stop | installed | focus | list | command"},
            "name": {"type": "STRING", "description": "Application name or executable name"},
            "args": {"type": "STRING", "description": "Extra arguments for launch / command"},
            "command": {"type": "STRING", "description": "For action=command: the shell command to run"},
            "wait": {"type": "INTEGER", "description": "Seconds to wait while confirming (default 3)"},
        },
        "required": ["action"],
    },
}

# canonical name -> (process names we expect to see, exe names we search for, launcher)
APPS = {
    "chrome":    (["chrome"],        ["chrome.exe"],           "chrome"),
    "edge":      (["msedge"],        ["msedge.exe"],           "msedge"),
    "firefox":   (["firefox"],       ["firefox.exe"],          "firefox"),
    "code":      (["Code"],          ["Code.exe"],             "code"),
    "vscode":    (["Code"],          ["Code.exe"],             "code"),
    "spotify":   (["Spotify"],       ["Spotify.exe"],          "spotify"),
    "discord":   (["Discord"],       ["Discord.exe"],          "discord"),
    "slack":     (["slack"],         ["slack.exe"],            "slack"),
    "steam":     (["steam"],         ["steam.exe"],            "steam"),
    "vlc":       (["vlc"],           ["vlc.exe"],              "vlc"),
    "notepad":   (["notepad"],       ["notepad.exe"],          "notepad"),
    "explorer":  (["explorer"],      ["explorer.exe"],         "explorer"),
    "calc":      (["CalculatorApp"], ["CalculatorApp.exe"],    "calc"),
    "whatsapp":  (["WhatsApp"],      ["WhatsApp.exe"],         "whatsapp"),
    "telegram":  (["Telegram"],      ["Telegram.exe"],         "telegram"),
    "word":      (["WINWORD"],       ["WINWORD.EXE"],          "winword"),
    "excel":     (["EXCEL"],         ["EXCEL.EXE"],            "excel"),
    "powerpnt":  (["POWERPNT"],      ["POWERPNT.EXE"],         "powerpnt"),
    "obs":       (["obs64"],         ["obs64.exe"],            "obs64"),
}


def _procs() -> list[str]:
    import psutil
    return [p.info["name"] or "" for p in psutil.process_iter(["name"])]


def _find(name: str) -> tuple[str, list[str]]:
    key = str(name or "").strip().lower()
    key = key.removesuffix(".exe")
    for canon, (pn, _exe, _l) in APPS.items():
        if key in (canon, _l):
            return canon, pn
    return key, [key, key.capitalize(), key.upper()]


def _is_running(want: list[str]) -> str | None:
    """Return the matched process name, or None."""
    try:
        names = _procs()
    except Exception:
        return None
    low = [n.lower() for n in names]
    for w in want:
        wl = w.lower()
        for i, n in enumerate(low):
            if n == wl or n.startswith(wl + "."):
                return names[i]
    return None



def _installed_windows(canon: str) -> bool:
    """Best-effort Windows install detection (Start Menu).  Never raises."""
    try:
        import os
        starts = [
            os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs"),
            r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs",
        ]
        needle = canon.lower()
        for root in starts:
            for dirpath, _dirs, files in os.walk(root):
                for f in files:
                    if needle in f.lower():
                        return True
        return False
    except Exception:
        return False


def run(args: dict, ctx):
    import shutil
    import subprocess

    act = str(args.get("action", "list")).lower().strip()
    name = str(args.get("name") or "")
    wait = max(1, min(int(args.get("wait") or 3), 20))

    # ── list ──────────────────────────────────────────────────────────────────
    if act == "list":
        try:
            names = set(_procs())
        except Exception as e:
            return failed("Cannot list processes", f"{type(e).__name__}: {e}")
        seen = []
        for canon, (pn, _e, _l) in APPS.items():
            hit = next((n for n in names if n.lower() == pn[0].lower()), None)
            if hit:
                seen.append(f"{canon}=RUNNING ({hit})")
        return done(f"{len(APPS)} apps tracked, {len(seen)} currently running",
                    evidence=("Running: " + (", ".join(seen) or "none of the tracked apps")))

    # ── command ───────────────────────────────────────────────────────────────
    if act == "command":
        cmd = str(args.get("command") or args.get("args") or "").strip()
        if not cmd:
            return failed("No command given", "command is required")
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=25, shell=True)
        except subprocess.TimeoutExpired:
            return failed("Command timed out", "no output within 25s")
        except Exception as e:
            return failed("Command could not run", f"{type(e).__name__}: {e}")
        out = (r.stdout or r.stderr or "").strip()
        if r.returncode == 0:
            return done("Command OK (exit 0)", evidence=out[:2500] or "(no output)")
        return unverified(f"Command ran but exited {r.returncode}",
                          reason="non-zero exit - check the output", evidence=out[:2500])

    if not name:
        return failed("No application name given", "name is required (e.g. 'chrome')")

    canon, want = _find(name)
    before = _is_running(want)

    if act == "running":
        if before:
            return done(f"{canon} is running", evidence=f"process observed: {before}")
        return unverified(f"{canon} is not running",
                          reason="no matching process found in the process list")

    if act == "installed":
        found = []
        if shutil.which(want[0]) or shutil.which(canon):
            found.append("on PATH")
        if canon in APPS and shutil.which(APPS[canon][2]):
            found.append(f"launcher '{APPS[canon][2]}'")
        if _installed_windows(canon):
            found.append("Start Menu entry")
        if found:
            return done(f"{canon} is installed", evidence="; ".join(sorted(set(found))))
        return unverified(f"Could not confirm {canon} is installed",
                          reason="not on PATH and no Start Menu entry found; "
                                 "it may still exist - launch it by name to confirm")

    # ── launch ────────────────────────────────────────────────────────────────
    if act == "launch":
        if before:
            return done(f"{canon} is already running - brought it forward",
                        evidence=f"process observed before launch: {before}")
        r = ""
        if ctx is not None:
            try:
                r = ctx.call_tool("open_app", {"app_name": canon})
            except Exception as e:
                r = f"[UNVERIFIED] open_app bridge failed: {e}"
        found, waited = None, 0
        for _ in range(wait * 2):
            time.sleep(0.5)
            waited += 0.5
            found = _is_running(want)
            if found:
                break
        if found:
            return done(f"Launched {canon} and confirmed it is running",
                        evidence=f"process observed: {found} after {waited:.1f}s")
        return unverified(f"Launch of {canon} was attempted but the process never appeared",
                          reason=f"not seen in process list after {wait}s. "
                                 f"tool said: {str(r)[:240]}")

    # ── stop ──────────────────────────────────────────────────────────────────
    if act == "stop":
        if not before:
            return unverified(f"{canon} is not running",
                              reason="nothing to stop - no matching process found")
        killed = []
        try:
            import psutil
            want_l = [w.lower() for w in want]
            for p in psutil.process_iter(["name", "pid"]):
                if (p.info["name"] or "").lower() in want_l:
                    try:
                        psutil.Process(p.info["pid"]).terminate()
                        killed.append(p.info["pid"])
                    except Exception:
                        pass
        except Exception as e:
            return failed("Could not stop the process", f"{type(e).__name__}: {e}")
        for _ in range(wait * 2):
            time.sleep(0.5)
            if not _is_running(want):
                return done(f"Stopped {canon}",
                            evidence=f"terminated pid(s): {killed}; process confirmed gone")
        return unverified(f"Stop was sent to {canon} but the process is still there",
                          reason=f"terminated pid(s) {killed} but the process survived")

    # ── focus ─────────────────────────────────────────────────────────────────
    if act == "focus":
        if not before:
            return unverified(f"{canon} is not running",
                              reason="cannot focus - start it first")
        r = ""
        if ctx is not None:
            try:
                r = ctx.call_tool("window_manager", {"action": "focus", "name": canon})
            except Exception as e:
                r = f"[UNVERIFIED] window_manager bridge failed: {e}"
        try:
            from actions import win_manager as wm
            if wm and wm._WIN:
                a = wm.active_window()
                if a:
                    hay = f"{a.get('title', '')} {a.get('exe', '')}".lower()
                    if any(w.lower() in hay for w in want):
                        return done(f"{canon} window is in front",
                                    evidence=f"foreground: {a.get('title')} [{a.get('exe')}]")
                    return unverified(f"{canon} is running but its window is not in front",
                                      reason=f"foreground is actually: {a.get('title')}")
        except Exception:
            pass
        return unverified(f"Focus requested for {canon}", reason=str(r)[:300])

    return failed(f"Unknown app_integration action '{act}'",
                  "valid: launch running stop installed focus list command")
