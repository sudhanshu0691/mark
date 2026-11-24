"""
Phone / video calls on the PC: pick up, decline, hang up, start a WhatsApp call.

Works through Windows UI Automation: it looks for the real Accept / Decline /
End buttons of the call popup and clicks them, then re-checks that the popup is
gone.  If there is no call popup, it says so instead of pretending.

Covers: WhatsApp Desktop, Phone Link (calls from your Android phone), Teams,
Skype, Zoom and the Windows toast banner.
"""
from __future__ import annotations

import os
import platform
import re
import time

from actions import uia, whatsapp as wa
from core.result import ActionResult, done, failed, unverified

_WIN = platform.system() == "Windows"

APP_WINDOWS = r"whatsapp|phone link|your phone|teams|skype|zoom|call|incoming"
ANSWER_RE = r"^(accept|answer|pick up|accept call|answer call|accept video|accept voice)\b|^accept$"
DECLINE_RE = r"^(decline|reject|dismiss|decline call|reject call)\b|^decline$"
HANGUP_RE = r"^(end call|hang up|hangup|end|leave|decline)\b"


def _candidate_windows():
    seen, out = set(), []
    for w in uia.windows(title_re=APP_WINDOWS) + uia.toast_windows():
        try:
            key = w.handle
        except Exception:
            key = id(w)
        if key not in seen:
            seen.add(key)
            out.append(w)
    return out


def _click_first(name_re: str, verb: str) -> ActionResult:
    ok, why = uia.available()
    if not ok:
        return failed(f"Cannot {verb} the call", why)
    hits = []
    for w in _candidate_windows():
        for name, btn in uia.buttons_matching(w, name_re):
            hits.append((w, name, btn))
    if not hits:
        return failed(f"Could not {verb} the call",
                      "no incoming-call button was found on screen: either nobody is calling right now, the call "
                      "banner already disappeared, or the app did not expose its buttons to Windows. "
                      "Tip: keep WhatsApp/Phone Link notifications ON and don't use Do-Not-Disturb.")
    w, name, btn = hits[0]
    try:
        btn.invoke()
    except Exception:
        try:
            btn.click_input()
        except Exception as e:
            return failed(f"Found the '{name}' button but could not click it", f"{type(e).__name__}: {e}")
    time.sleep(1.2)
    still = [1 for ww in _candidate_windows() for _ in uia.buttons_matching(ww, name_re)]
    if len(still) < len(hits):
        return done(f"Call: clicked '{name}'.", evidence="that button is gone after the click")
    return unverified(f"Clicked '{name}' on the call popup",
                      reason="the same button is still visible afterwards, so I can't confirm the call state changed")


def start_whatsapp_call(contact: str, video: bool) -> ActionResult:
    pre = wa._preflight()
    if pre:
        return pre
    phone = wa._phone_for(contact)
    if not phone:
        return failed(f"Cannot call {contact}",
                      "no phone number known for this name. Add it to config/contacts.json (country code, digits only).")
    win, err = wa._open_wa(f"whatsapp://send?phone={phone}")
    if not win:
        return failed(f"Cannot call {contact}", err)
    time.sleep(1.5)
    pat = r"video call" if video else r"(voice|audio) call|^call$"
    hits = uia.buttons_matching(win, pat)
    if not hits:
        return failed(f"Cannot call {contact}", "the call button was not found in the WhatsApp window (is this contact on WhatsApp?)")
    name, btn = hits[0]
    try:
        btn.invoke()
    except Exception:
        btn.click_input()
    time.sleep(3)
    ws = uia.windows(title_re=APP_WINDOWS)
    all_text = " ".join(" ".join(uia.texts(w, 80)) for w in ws).lower()
    if re.search(r"calling|ringing|connecting|call(ing)? \w+", all_text):
        return done(f"{'Video' if video else 'Voice'} call to {contact} started.", evidence="call screen shows calling/ringing")
    return unverified(f"Pressed '{name}' for {contact}", reason="could not see a 'calling/ringing' screen afterwards")


def dial_number(number: str) -> ActionResult:
    digits = re.sub(r"[^\d+]", "", number)
    if len(digits) < 5:
        return failed("Cannot dial", f"'{number}' does not look like a phone number")
    try:
        os.startfile(f"tel:{digits}")  # type: ignore[attr-defined]
    except Exception as e:
        return failed(f"Cannot dial {digits}", f"no app is registered for tel: links (install/link Phone Link). {e}")
    return unverified(f"Opened the dialer for {digits}", reason="Windows hands tel: to Phone Link; whether the phone actually dialled is not visible from here")


def calls(parameters: dict | None = None, player=None, **_) -> str:
    a = parameters or {}
    action = str(a.get("action", "")).lower().strip()
    if player:
        player.write_log(f"[call] {action}")
    try:
        if not _WIN:
            r = failed("Call action not run", "call control works only on Windows")
        elif action in ("answer", "accept", "pick"):
            r = _click_first(ANSWER_RE, "answer")
        elif action in ("decline", "reject"):
            r = _click_first(DECLINE_RE, "decline")
        elif action in ("hangup", "end"):
            r = _click_first(HANGUP_RE, "end")
        elif action in ("call", "voice", "video"):
            r = start_whatsapp_call(str(a.get("contact", "")), video=bool(a.get("video")) or action == "video")
        elif action == "dial":
            r = dial_number(str(a.get("number", "")))
        else:
            r = failed(f"Unknown call action '{action}'", "valid: answer decline hangup call video dial")
    except Exception as e:
        r = failed(f"Call action '{action}' crashed", f"{type(e).__name__}: {e}")
    if player:
        player.write_log(f"[call] {r.status}: {r.message[:90]}")
    return r.to_llm()
