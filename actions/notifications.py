"""

Windows notification reader.

Reads the Windows notification database (wpndatabase.db) - the same place the
notification bell / Action Center gets its data from - so Mark can tell you
"WhatsApp: Rahul - kal milte hain?" the moment a toast appears.

Requirements: Windows 10/11, notifications enabled for the app (Settings >
System > Notifications), and for WhatsApp "show message preview" ON.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import sqlite3
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from collections import deque
from pathlib import Path
from typing import Callable

from core.result import ActionResult, done, failed, unverified

_WIN = platform.system() == "Windows"
BASE_DIR = Path(__file__).resolve().parent.parent
LOG_FILE = BASE_DIR / "memory" / "notifications.json"

_FRIENDLY = {
    "whatsapp": "WhatsApp", "telegram": "Telegram", "signal": "Signal", "discord": "Discord",
    "instagram": "Instagram", "messenger": "Messenger", "outlook": "Outlook", "gmail": "Gmail",
    "chrome": "Chrome", "msedge": "Edge", "edge": "Edge", "teams": "Teams", "skype": "Skype",
    "yourphone": "Phone Link", "phonelink": "Phone Link", "slack": "Slack", "zoom": "Zoom",
}
_CALL_WORDS = ("calling", "incoming call", "incoming voice", "incoming video", "video call", "voice call", "ringing")
_CALL_BUTTONS = ("accept", "answer", "decline", "reject")


def _db_path() -> Path | None:
    la = os.environ.get("LOCALAPPDATA")
    if not la:
        return None
    p = Path(la) / "Microsoft" / "Windows" / "Notifications" / "wpndatabase.db"
    return p if p.exists() else None


def _friendly_app(primary_id: str) -> str:
    low = (primary_id or "").lower()
    for k, v in _FRIENDLY.items():
        if k in low:
            return v
    tail = (primary_id or "unknown").split("!")[0].split("_")[0].split(".")[-1]
    return tail or "unknown"


def _parse_payload(payload) -> tuple[list[str], list[str]]:
    if isinstance(payload, bytes):
        for enc in ("utf-8", "utf-16"):
            try:
                payload = payload.decode(enc)
                break
            except Exception:
                continue
        else:
            payload = payload.decode("utf-8", errors="ignore")
    texts, buttons = [], []
    try:
        root = ET.fromstring(str(payload).strip("\x00 ﻿"))
        for t in root.iter("text"):
            if t.text and t.text.strip():
                texts.append(t.text.strip())
        for a in root.iter("action"):
            c = a.attrib.get("content")
            if c:
                buttons.append(c)
    except Exception:
        pass
    return texts, buttons


def _snapshot_db(src: Path) -> Path:
    """Copy db + wal so we can read without fighting Windows' lock."""
    tmp = Path(tempfile.mkdtemp(prefix="jv_wpn_"))
    dst = tmp / "wpn.db"
    shutil.copy2(src, dst)
    for ext in ("-wal", "-shm"):
        s = Path(str(src) + ext)
        if s.exists():
            try:
                shutil.copy2(s, Path(str(dst) + ext))
            except OSError:
                pass
    return dst


def _query_since(last_id: int) -> list[dict]:
    src = _db_path()
    if not src:
        raise FileNotFoundError("wpndatabase.db not found")
    snap = _snapshot_db(src)
    try:
        con = sqlite3.connect(f"file:{snap}?mode=ro", uri=True)
        con.text_factory = bytes
        rows = con.execute(
            "SELECT n.Id, n.Payload, n.ArrivalTime, h.PrimaryId "
            "FROM Notification n JOIN NotificationHandler h ON n.HandlerId = h.RecordId "
            "WHERE n.Id > ? AND n.Type = 'toast' ORDER BY n.Id",
            (last_id,),
        ).fetchall()
        con.close()
    finally:
        shutil.rmtree(snap.parent, ignore_errors=True)
    out = []
    for nid, payload, arrival, pid in rows:
        texts, buttons = _parse_payload(payload)
        pid_s = pid.decode("utf-8", "ignore") if isinstance(pid, bytes) else str(pid)
        joined = " ".join(texts).lower()
        is_call = any(w in joined for w in _CALL_WORDS) or any(b.lower() in _CALL_BUTTONS for b in buttons)
        out.append({
            "id": int(nid), "app": _friendly_app(pid_s), "texts": texts, "buttons": buttons,
            "is_call": is_call, "ts": time.time(),
        })
    return out


class NotificationWatcher:
    def __init__(self):
        self.history: deque[dict] = deque(maxlen=200)
        self.listeners: list[Callable[[dict], None]] = []
        self.announce = True
        self.apps_filter: set[str] = set()      # empty = all apps
        self.last_error: str = ""
        self._last_id = 0
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.running = False

    def start(self) -> ActionResult:
        if not _WIN:
            self.last_error = "notification reading works only on Windows"
            return failed("Notification watcher not started", self.last_error)
        if self.running:
            return done("Notification watcher already running.")
        if not _db_path():
            self.last_error = "Windows notification database not found (Windows 10/11 only)"
            return failed("Notification watcher not started", self.last_error)
        try:
            existing = _query_since(0)
            self._last_id = max((n["id"] for n in existing), default=0)
        except Exception as e:
            self.last_error = f"cannot read notification database: {e}"
            return failed("Notification watcher not started", self.last_error)
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="notif-watch")
        self._thread.start()
        self.running = True
        return done("Notification watcher running.", evidence=f"baseline id {self._last_id}")

    def stop(self):
        self._stop.set()
        self.running = False

    def _loop(self):
        fails = 0
        while not self._stop.is_set():
            try:
                for n in _query_since(self._last_id):
                    self._last_id = max(self._last_id, n["id"])
                    self.history.append(n)
                    self._persist()
                    if self.apps_filter and n["app"].lower() not in self.apps_filter:
                        continue
                    for cb in list(self.listeners):
                        try:
                            cb(n)
                        except Exception as e:
                            print(f"[Notify] listener error: {e}")
                fails = 0
                self.last_error = ""
            except Exception as e:
                fails += 1
                self.last_error = str(e)
                if fails == 5:
                    print(f"[Notify] cannot read notifications: {e}")
            self._stop.wait(2.0)
        self.running = False

    def _persist(self):
        try:
            LOG_FILE.write_text(json.dumps(list(self.history)[-100:], ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:
            pass


WATCHER = NotificationWatcher()


def describe(n: dict) -> str:
    body = " - ".join(n["texts"]) if n["texts"] else "(no text)"
    kind = "CALL " if n["is_call"] else ""
    return f"{kind}{n['app']}: {body}"


def notifications(parameters: dict | None = None, player=None, **_) -> str:
    a = parameters or {}
    action = str(a.get("action", "recent")).lower().strip()
    app = str(a.get("app", "")).strip().lower()
    if player:
        player.write_log(f"[notif] {action}")
    try:
        if action in ("start", "enable"):
            r = WATCHER.start()
        elif action in ("announce_on", "announce_off"):
            WATCHER.announce = action == "announce_on"
            r = done(f"Spoken notification alerts are now {'ON' if WATCHER.announce else 'OFF'}.")
        elif action in ("recent", "read", "summary"):
            if not WATCHER.running:
                st = WATCHER.start()
                if not st.ok:
                    return failed("Cannot read notifications", st.reason).to_llm()
            items = [n for n in WATCHER.history if not app or app in n["app"].lower()]
            n = int(a.get("count", 10))
            items = items[-n:]
            if not items:
                r = done("No new notifications since Mark started.",
                         evidence="watcher is running" + (f"; last read error: {WATCHER.last_error}" if WATCHER.last_error else ""))
            else:
                r = done("Notifications (oldest first):\n" + "\n".join(describe(i) for i in items))
        elif action == "clear":
            WATCHER.history.clear()
            r = done("Mark' notification history cleared (Windows' own bell is untouched).")
        elif action == "status":
            r = done(f"watcher running={WATCHER.running}, spoken alerts={'ON' if WATCHER.announce else 'OFF'}, "
                     f"stored={len(WATCHER.history)}, last_error='{WATCHER.last_error}'")
        else:
            r = failed(f"Unknown notification action '{action}'", "valid: recent start announce_on announce_off clear status")
    except Exception as e:
        r = failed("Notification action crashed", f"{type(e).__name__}: {e}")
    return r.to_llm()
