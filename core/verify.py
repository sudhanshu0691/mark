"""
core/verify.py - the verifier that the brain MUST run; the model cannot skip it.

A tool result can say [DONE] / [UNVERIFIED] / [FAILED] on its own. This module adds an
independent check afterwards, cheapest first:

    1. direct   file exists / window is open (no AI, no screenshot)
    2. screen   expected text or window is really on screen (UI Automation / OCR via screen_read)
    3. vision   an explicitly supplied verifier looks at a screenshot and answers YES / NO / UNSURE

verify_step() returns a Verdict. The brain turns a failed verdict into [FAILED] and an
inconclusive one into [UNVERIFIED], so "done" is only said when something independent agrees.
Everything that touches the OS is injected, so the logic is unit-tested without Windows.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

PASS, FAIL, SKIP = "pass", "fail", "skip"

# Tools that change something the user could see. Others (search, status...) need no check.
GUI_TOOLS = {"open_app", "browser_control", "computer_control", "computer_settings", "desktop_control",
             "window_manager", "whatsapp", "calls", "youtube_video", "send_message"}
FILE_TOOLS = {"file_controller"}
CHECKED_TOOLS = GUI_TOOLS | FILE_TOOLS | {"software_manager", "dev_agent", "code_helper", "file_processor"}


@dataclass
class Verdict:
    status: str            # pass | fail | skip
    method: str            # direct | screen | vision | none
    detail: str = ""
    proof: str = ""        # path of a saved screenshot, if any

    @property
    def ok(self) -> bool:
        return self.status == PASS

    def line(self) -> str:
        extra = f" proof={self.proof}" if self.proof else ""
        return f"{self.status.upper()} via {self.method}: {self.detail}{extra}"


@dataclass
class Deps:
    """Everything environment-specific. Defaults are wired lazily; tests pass fakes."""
    list_windows: Callable[[], list[dict]] | None = None
    resolve_path: Callable[[str], Path] | None = None
    dispatch: Callable[[str, dict], str] | None = None          # runs a tool, e.g. screen_read
    screenshot: Callable[[], bytes] | None = None                # PNG bytes of the screen
    vision: Callable[[bytes, str], str] | None = None
    save_proof: Callable[[bytes, str], str] | None = None
    sleep: Callable[[float], None] = time.sleep


def default_deps(dispatch: Callable[[str, dict], str] | None = None) -> Deps:
    d = Deps(dispatch=dispatch)
    try:
        from actions import win_manager as wm
        d.list_windows = wm.list_windows
    except Exception:
        pass
    try:
        from actions import fs_full
        d.resolve_path = fs_full.resolve
    except Exception:
        pass

    return d


_VISION_SYSTEM = ("You check whether an action succeeded by looking at ONE screenshot. "
                  "Answer with exactly one of: 'YES: <reason>', 'NO: <reason>', 'UNSURE: <reason>'. "
                  "Say YES only if you can clearly see the evidence. Never guess.")


# ----------------------------------------------------------------------------- expectations
def expected_paths(tool: str, args: dict) -> list[tuple[str, bool]]:
    """(raw path, must_exist) pairs that must hold after a file tool ran."""
    if tool != "file_controller":
        return []
    act = str(args.get("action", "")).lower()
    path = str(args.get("path") or "")
    name = str(args.get("name") or "")
    dest = str(args.get("destination") or "")
    if act in ("write", "append", "mkdir"):
        return [(f"{path}/{name}" if name and path else (path or name), True)]
    if act in ("copy", "move", "zip", "unzip"):
        return [(dest, True)] if dest else []
    if act == "rename" and args.get("new_name"):
        return [(str(Path(path).parent / str(args["new_name"])), True)]
    if act == "delete" and path:
        return [(path, False)]
    return []


def expected_window(tool: str, args: dict) -> str:
    """Regex that the front window / title should match after a GUI tool ran."""
    if tool == "open_app":
        app = str(args.get("app_name") or "").strip()
        return re.escape(app) if app else ""
    if tool == "browser_control":
        url = str(args.get("url") or "")
        host = urlparse(url if "//" in url else "//" + url).netloc.replace("www.", "")
        return re.escape(host.split(".")[0]) if host else ""
    if tool == "youtube_video":
        return "youtube"
    if tool == "whatsapp":
        return "whatsapp"
    return ""


def expected_text(tool: str, args: dict) -> list[str]:
    if tool == "whatsapp" and str(args.get("action")) == "send" and args.get("message"):
        return [str(args["message"])[:40]]
    return []


def describe_step(tool: str, args: dict) -> str:
    keep = {k: v for k, v in args.items() if v not in (None, "", [])}
    return f"{tool} {str(keep)[:200]}"


# ----------------------------------------------------------------------------- the ladder
def _direct(tool: str, args: dict, d: Deps) -> Verdict | None:
    pairs = expected_paths(tool, args)
    if pairs and d.resolve_path:
        bad = []
        for raw, must_exist in pairs:
            try:
                p = d.resolve_path(raw)
            except Exception as e:
                bad.append(f"{raw}: cannot resolve ({e})")
                continue
            if p.exists() != must_exist:
                bad.append(f"{p} {'is missing' if must_exist else 'still exists'}")
        if bad:
            return Verdict(FAIL, "direct", "; ".join(bad))
        return Verdict(PASS, "direct", "; ".join(f"{r} {'exists' if m else 'gone'}" for r, m in pairs))

    win = expected_window(tool, args)
    if win and d.list_windows:
        wins = d.list_windows()
        if wins:
            for w in wins:
                if re.search(win, f"{w.get('title', '')} {w.get('exe', '')}", re.I):
                    return Verdict(PASS, "direct", f"window /{win}/ is open: {w.get('title', '')[:60]}")
            return Verdict(FAIL, "direct", f"no open window matches /{win}/")
    return None


def _screen(tool: str, args: dict, d: Deps, wait: float = 3.0) -> Verdict | None:
    win, texts = expected_window(tool, args), expected_text(tool, args)
    if not (win or texts) or not d.dispatch:
        return None
    out = d.dispatch("screen_read", {"mode": "verify", "expect_window": win, "expect_text": texts, "wait": wait})
    head = (out or "").splitlines()[0] if out else ""
    if head.startswith("[DONE]"):
        return Verdict(PASS, "screen", head[:160])
    if head.startswith("[FAILED]"):
        return Verdict(FAIL, "screen", head[:160])
    return None


def _vision(tool: str, args: dict, d: Deps, goal: str = "") -> Verdict | None:
    if not (d.screenshot and d.vision):
        return None
    try:
        img = d.screenshot()
        proof = d.save_proof(img, tool) if d.save_proof else ""
        what = goal or describe_step(tool, args)
        ans = (d.vision(img, f"Action just performed: {what}\nDoes the screen show that it worked? "
                             "Reply YES / NO / UNSURE with one short reason.") or "").strip()
    except Exception as e:
        return Verdict(SKIP, "vision", f"vision check unavailable: {type(e).__name__}: {e}")
    m = re.match(r"\W*(yes|no|unsure)\b[:\- ]*(.*)", ans, re.I | re.S)
    if not m:
        return Verdict(SKIP, "vision", f"unclear answer: {ans[:100]}", proof)
    word, why = m.group(1).lower(), m.group(2).strip()[:200]
    return Verdict({"yes": PASS, "no": FAIL}.get(word, SKIP), "vision", why or word, proof)


def verify_step(tool: str, args: dict, d: Deps, goal: str = "") -> Verdict:
    """Run the ladder for ONE finished tool call. First conclusive rung wins."""
    from core.interaction_verify import supports
    if supports(tool, args):
        return Verdict(SKIP, "none", "viewport verification is performed during execution")
    if tool not in CHECKED_TOOLS:
        return Verdict(SKIP, "none", "read-only or self-verifying tool")
    if tool == "file_controller" and str(args.get("action", "")).lower() == "open":
        from core.action_confirmation import confirm_result
        result = confirm_result(tool, args, "[UNVERIFIED] Open requested")
        if result.startswith("[DONE]"):
            return Verdict(PASS, "window", result.splitlines()[-1])
        if result.startswith("[FAILED]"):
            return Verdict(FAIL, "window", result.splitlines()[-1])
        return Verdict(SKIP, "window", result.splitlines()[-1])
    for rung in (lambda: _direct(tool, args, d), lambda: _screen(tool, args, d)):
        v = rung()
        if v is not None:
            return v
    if tool in GUI_TOOLS:
        v = _vision(tool, args, d, goal)
        if v is not None:
            return v
    return Verdict(SKIP, "none", "no independent check was possible")


def final_screen_check(goal: str, d: Deps) -> Verdict:
    """Last look: does the screen agree that the whole goal is finished?"""
    v = _vision("goal", {}, d, goal=f"GOAL: {goal}")
    return v or Verdict(SKIP, "none", "no screen check available")
