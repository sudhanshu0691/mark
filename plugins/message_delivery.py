"""
Message Delivery Status - VERIFIED delivery/read receipts for messaging platforms.

There is no universal, standardized API for Telegram/Signal/WhatsApp delivery status
from a desktop app, so this plugin does the honest thing: it looks at the actual
Windows window / screen (UI Automation + OCR) for the platform's own delivery
indicators and reports what it can PROVE.  It never guesses.

  PASS         -> a tick / "Delivered" / "Read" indicator was actually seen
  UNVERIFIED   -> nothing on screen proved or disproved delivery (says exactly why)
  FAILED       -> the app/chat could not be found at all
"""
from __future__ import annotations

import re

from core.result import done, failed, unverified

NAME = "message_delivery"
DESCRIPTION = ("Check whether a message was delivered/read on WhatsApp, Telegram, Signal or SMS by "
               "reading the real window/screen indicators. Returns PASS only when seen.")
METADATA = {"version": "1.0.0",
            "capabilities": ["delivery", "receipt", "whatsapp", "telegram", "signal", "status"],
            "permissions": [], "timeout": 45}

DECLARATION = {
    "name": "message_delivery",
    "description": ("Verify message delivery status. action=check. "
                    "platform = whatsapp | telegram | signal | sms. "
                    "contact = chat/contact name to look for (optional but recommended)."),
    "parameters": {"type": "OBJECT", "properties": {
        "action": {"type": "STRING", "description": "check"},
        "platform": {"type": "STRING", "description": "whatsapp | telegram | signal | sms"},
        "contact": {"type": "STRING", "description": "Contact or chat name to inspect"},
        "message": {"type": "STRING", "description": "First words of the message (helps find the bubble)"},
        "wait": {"type": "INTEGER", "description": "Seconds to keep re-checking (default 4)"},
    }, "required": ["action"]},
}

# (regex, human label) for a DELIVERED signal, and separately for READ
DELIVERED = [
    (r"\b(delivered)\b", "the word 'Delivered' is visible"),
    (r"\b\d{1,2}:\d{2}\s*(AM|PM)?\b.{0,24}✓✓", "double tick next to a timestamp"),
    (r"✓✓", "double tick marks"),
    (r"\bsent\b", "the word 'Sent' is visible"),
    (r"\bentregado\b|\blivré\b|\bzugestellt\b", "localised 'delivered' text"),
]
READ = [
    (r"\b(read)\b", "the word 'Read' is visible"),
    (r"\bread at\b", "'Read at' is visible"),
    (r"\bgelesen\b", "'gelesen' (read) is visible"),
]
NOT_SENT = [
    (r"\bfailed to send\b|\bcouldn't send\b|\bnicht gesendet\b", "'failed to send' is visible"),
    (r"\bnot sent\b", "'not sent' is visible"),
    (r"\bwaiting for network\b", "'waiting for network' is visible"),
]

WIN_HINTS = {
    "whatsapp": ("whatsapp",),
    "telegram": ("telegram",),
    "signal": ("signal",),
    "sms": ("phone link", "your phone", "messages"),
}


def _ocr_window(platform: str) -> tuple[str, str, str]:
    """(foreground window title, exe, visible text) - best effort, never raises."""
    title = exe = ""
    try:
        from actions import win_manager as wm
        if wm and wm._WIN:
            a = wm.active_window() or {}
            title, exe = str(a.get("title") or ""), str(a.get("exe") or "")
    except Exception:
        pass
    text = ""
    try:
        from actions.screen_read import _visible_text, grab_full, _ocr_text
        try:
            from actions import win_manager as wm
            hwnd = None
            if wm and wm._WIN:
                a = wm.active_window() or {}
                hwnd = a.get("hwnd")
        except Exception:
            hwnd = None
        combined, src = _visible_text(hwnd)
        if combined:
            text = combined
        elif text == "":
            text = _ocr_text()
    except Exception as e:
        text = f"[TEXT_UNAVAILABLE] {type(e).__name__}: {e}"
    return title, exe, text


def _match(patterns, hay: str) -> str | None:
    for pat, label in patterns:
        if re.search(pat, hay, re.I):
            return label
    return None


def run(args: dict, ctx):
    act = str(args.get("action", "check")).lower().strip()
    if act not in ("check", "status", "verify"):
        return failed(f"Unknown message_delivery action '{act}'",
                      "valid: check  (platform, contact and message improve accuracy)")
    platform = str(args.get("platform") or "whatsapp").lower()
    contact = str(args.get("contact") or "").strip()
    message = str(args.get("message") or "").strip()
    wait = max(0, min(int(args.get("wait") or 4), 20))
    hints = WIN_HINTS.get(platform, (platform,))

    # Try a few times: the chat window may need to be focused first.
    title = exe = text = ""
    seen_foreground = False
    attempts = max(1, wait * 2)
    for i in range(attempts):
        title, exe, text = _ocr_window(platform)
        hay = f"{title} {exe}".lower()
        if any(h in hay for h in hints):
            seen_foreground = True
            break
        if i == 0 and ctx is not None:
            # bring the app to the front once so its ticks become visible
            try:
                ctx.call_tool("open_app", {"app_name": platform})
            except Exception:
                pass
        import time as _t
        _t.sleep(0.5)

    if text.startswith("[TEXT_UNAVAILABLE]"):
        return unverified(
            f"Could not read the {platform} window to check delivery",
            reason=f"screen text is unavailable: {text[:160]}. "
                   f"Install an OCR engine (pip install winocr, or Tesseract) to enable "
                   f"delivery verification; otherwise this cannot be confirmed.",
            evidence=f"foreground: {title} [{exe}]",
        )

    hay_full = f"{title}\n{text}"
    read_hit = _match(READ, hay_full)
    fail_hit = _match(NOT_SENT, hay_full)
    deliv_hit = _match(DELIVERED, hay_full)

    ctx_bits = []
    if contact:
        ctx_bits.append(f"contact={contact}")
    if message:
        ctx_bits.append(f"message='{message[:40]}'")

    if fail_hit:
        return failed(
            f"{platform}: the message reports a SEND FAILURE",
            reason=f"seen on screen: {fail_hit}. The message did NOT reach the recipient. "
                   f"{''.join(ctx_bits)}",
        )

    if not seen_foreground:
        # We read *something*, but the chat app is not in front - so nothing proves delivery.
        return unverified(
            f"{platform} is not in the foreground, so delivery could not be confirmed",
            reason=f"foreground window was '{title or 'unknown'}' [{exe or '?'}]; "
                   f"a delivery tick must be read from the chat window itself. "
                   f"Open the chat and ask again.",
            evidence=f"text read: {text[:400] or '(none)'}",
        )

    if read_hit:
        return done(f"{platform}: message shows as READ",
                    evidence=f"proof on screen: {read_hit}\nforeground: {title} [{exe}]\n{text[:600]}")

    if deliv_hit:
        return done(f"{platform}: message shows as DELIVERED",
                    evidence=f"proof on screen: {deliv_hit}\nforeground: {title} [{exe}]\n{text[:600]}")

    return unverified(
        f"{platform}: no delivery indicator visible yet",
        reason=f"the {platform} window is in front but no tick / 'Delivered' / 'Read' marker "
               f"was recognised. This usually means the message is still sending, the chat is "
               f"not open, or OCR misread the ticks. Nothing here is claimed as delivered.",
        evidence=f"foreground: {title} [{exe}]\ntext read: {text[:600] or '(none)'}",
    )

