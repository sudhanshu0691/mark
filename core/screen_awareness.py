"""Local, bounded desktop observation; never persists frames or UI text."""
import logging
import threading
import time

log = logging.getLogger(__name__)

class ScreenMonitor:
    def __init__(self, interval=2.0):
        self.interval = max(0.5, float(interval))
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._enabled = True
        self._thread = None
        self._state = {"timestamp": 0, "active": None, "windows": [], "event": None,
                       "task_id": None, "expected": None, "verification": None}

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="mark-screen-monitor", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def set_enabled(self, enabled):
        with self._lock:
            self._enabled = bool(enabled)

    def snapshot(self):
        with self._lock:
            import copy
            return copy.deepcopy(self._state)

    def task(self, task_id, expected, verification=None):
        with self._lock:
            self._state.update(task_id=task_id, expected=expected, verification=verification)

    def _run(self):
        previous = None
        while not self._stop.is_set():
            with self._lock:
                enabled = self._enabled
            if enabled:
                try:
                    from actions import win_manager as wm
                    active, windows = wm.active_window(), wm.list_windows()
                    signature = (active.get("hwnd") if active else None,
                                 active.get("title") if active else None,
                                 tuple(sorted(w.get("hwnd", 0) for w in windows)))
                    now = time.monotonic()
                    with self._lock:
                        self._state.update(timestamp=now, active=active, windows=windows)
                        if signature != previous:
                            self._state["event"] = {"type": "windows_changed", "timestamp": now,
                                                    "task_id": self._state["task_id"]}
                    previous = signature
                except Exception as exc:
                    log.debug("Local screen observation unavailable: %s", exc)
            self._stop.wait(self.interval)
