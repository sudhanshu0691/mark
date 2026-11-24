"""
WhatsApp Desktop (Windows) - send / read / call, with verification.

Rules this module follows so Mark never lies:
  * a message is only called sent (DONE) when it is visible as a chat bubble
    after sending;
  * when the target chat can't be confirmed, it does NOT send - it fails with
    the reason (so a message never goes to the wrong person);
  * contacts: put "name": "919999999999" in config/contacts.json for 100%
    reliable targeting (uses the official whatsapp:// link).
"""
from __future__ import annotations

import difflib
import json
import os
import platform
import re
import time
import urllib.parse
from pathlib import Path

from actions import uia
from core.result import ActionResult, done, failed, unverified

_WIN = platform.system() == "Windows"
BASE_DIR = Path(__file__).resolve().parent.parent
CONTACTS = BASE_DIR / "config" / "contacts.json"
WA_TITLE = r"^WhatsApp"

try:
    import pyautogui
    pyautogui.FAILSAFE = True
    _GUI = True
except Exception:
    _GUI = False
try:
    import pyperclip
except Exception:
    pyperclip = None


def _contacts() -> dict[str, str]:
    try:
        d = json.loads(CONTACTS.read_text(encoding="utf-8")).get("contacts", {})
        return {k.lower(): re.sub(r"\D", "", v) for k, v in d.items() if v}
    except Exception:
        return {}


def _phone_for(name: str) -> str | None:
    digits = re.sub(r"\D", "", name)
    if len(digits) >= 8 and len(digits) >= len(re.sub(r"[\s+\-()]", "", name)) - 1:
        return digits
    c = _contacts()
    key = name.lower().strip()
    if key in c:
        return c[key]
    m = difflib.get_close_matches(key, list(c), n=1, cutoff=0.8)
    return c[m[0]] if m else None


def _preflight() -> ActionResult | None:
    if not _WIN:
        return failed("WhatsApp action not run", "WhatsApp Desktop automation works only on Windows")
    ok, why = uia.available()
    if not ok:
        return failed("WhatsApp action not run", why)
    if not _GUI:
        return failed("WhatsApp action not run", "pyautogui is not installed (pip install pyautogui)")
    return None


def _open_wa(url: str = "whatsapp:") -> tuple[object | None, str]:
    try:
        os.startfile(url)  # type: ignore[attr-defined]
    except OSError:
        return None, ("WhatsApp Desktop is not installed (get 'WhatsApp' from the Microsoft Store) "
                      "or the whatsapp:// link is not registered")
    win = uia.find_window(WA_TITLE, timeout=20)
    if not win:
        return None, "WhatsApp window did not appear within 20s (not logged in, or app is still starting)"
    uia.focus(win)
    time.sleep(1.2)
    return win, ""


def _bubble_visible(win, text: str) -> bool:
    """Is `text` visible as a chat message (not just sitting in the compose box)?"""
    probe = text.strip().splitlines()[0][:40].lower()
    if not probe:
        return False
    try:
        for ctype in ("Text", "ListItem", "Group", "Document"):
            for el in win.descendants(control_type=ctype):
                try:
                    if probe in (el.window_text() or "").lower():
                        return True
                except Exception:
                    continue
    except Exception:
        pass
    return False


def _compose_text(win) -> str:
    try:
        for e in win.descendants(control_type="Edit"):
            v = e.window_text() or ""
            if v:
                return v
    except Exception:
        pass
    return ""


def _chat_header_has(win, name: str) -> bool:
    n = name.lower().strip()
    return any(n in t.lower() for t in uia.texts(win, 120)[:60])


def send_message(receiver: str, message: str) -> ActionResult:
    pre = _preflight()
    if pre:
        return pre
    if not receiver or not message:
        return failed("WhatsApp message not sent", "receiver and message are both required")

    phone = _phone_for(receiver)
    if phone:
        win, err = _open_wa(f"whatsapp://send?phone={phone}&text={urllib.parse.quote(message)}")
        if not win:
            return failed(f"Message to {receiver} not sent", err)
        time.sleep(2.0)
        if not _compose_text(win).strip() and not _bubble_visible(win, message):
            # deep link did not prefill the text; paste it ourselves
            if pyperclip:
                pyperclip.copy(message)
                pyautogui.hotkey("ctrl", "v")
                time.sleep(0.4)
            else:
                return failed(f"Message to {receiver} not sent", "WhatsApp did not prefill the text and pyperclip is missing")
    else:
        win, err = _open_wa()
        if not win:
            return failed(f"Message to {receiver} not sent", err)
        pyautogui.hotkey("ctrl", "f")
        time.sleep(0.5)
        if pyperclip:
            pyperclip.copy(receiver)
            pyautogui.hotkey("ctrl", "v")
        else:
            pyautogui.write(receiver, interval=0.03)
        time.sleep(1.5)
        pyautogui.press("down")
        time.sleep(0.2)
        pyautogui.press("enter")
        time.sleep(1.5)
        win = uia.find_window(WA_TITLE) or win
        if not _chat_header_has(win, receiver):
            return failed(f"Message to {receiver} not sent",
                          f"could not confirm that the open chat is '{receiver}', so nothing was sent "
                          f"(to avoid texting the wrong person). Add their number to config/contacts.json.")
        if pyperclip:
            pyperclip.copy(message)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.4)
        else:
            pyautogui.write(message, interval=0.02)

    uia.focus(win)
    time.sleep(0.3)
    pyautogui.press("enter")
    time.sleep(2.0)
    win = uia.find_window(WA_TITLE) or win

    if _bubble_visible(win, message) and message.strip().splitlines()[0][:40].lower() not in _compose_text(win).lower():
        return done(f"WhatsApp message to {receiver} sent.",
                    evidence="message is visible as a chat bubble (delivery/read ticks not checked)")
    if _compose_text(win).strip():
        return failed(f"Message to {receiver} NOT sent", "the text is still sitting in the compose box (Enter did not send it)")
    return unverified(f"WhatsApp message to {receiver} was submitted",
                      reason="could not read the chat window afterwards to confirm the bubble (WhatsApp hides its UI tree)")


def read_chat(max_items: int = 15) -> ActionResult:
    pre = _preflight()
    if pre:
        return pre
    win = uia.find_window(WA_TITLE)
    if not win:
        return failed("Cannot read WhatsApp", "WhatsApp window is not open")
    ts = uia.texts(win, 600)
    if not ts:
        return failed("Cannot read WhatsApp", "the window exposes no readable text (maybe minimized or still loading)")
    return done("Visible WhatsApp text (latest last):\n" + "\n".join(ts[-max_items:]))


def open_chat(contact: str) -> ActionResult:
    pre = _preflight()
    if pre:
        return pre
    phone = _phone_for(contact)
    win, err = _open_wa(f"whatsapp://send?phone={phone}" if phone else "whatsapp:")
    if not win:
        return failed(f"Cannot open chat with {contact}", err)
    if phone:
        return unverified(f"Opened WhatsApp chat for {contact}", reason="chat opened via official link; header not re-checked")
    return unverified(f"Opened WhatsApp", reason=f"no number stored for '{contact}', so the chat itself was not selected")


def whatsapp(parameters: dict | None = None, player=None, **_) -> str:
    a = parameters or {}
    action = str(a.get("action", "send")).lower().strip()
    if player:
        player.write_log(f"[whatsapp] {action} {a.get('contact', '')}")
    try:
        if action == "send":
            r = send_message(str(a.get("contact", "")).strip(), str(a.get("message", "")).strip())
        elif action == "read":
            r = read_chat(int(a.get("count", 15)))
        elif action == "open":
            r = open_chat(str(a.get("contact", "")))
        else:
            r = failed(f"Unknown WhatsApp action '{action}'", "valid: send read open")
    except Exception as e:
        r = failed(f"WhatsApp '{action}' crashed", f"{type(e).__name__}: {e}")
    if player:
        player.write_log(f"[whatsapp] {r.status}: {r.message[:90]}")
    return r.to_llm()
