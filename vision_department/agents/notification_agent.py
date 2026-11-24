"""Cooldown-aware notification dispatch to MARK's host dashboard."""
from __future__ import annotations

import time


class VisionNotificationAgent:
    name = "vision_notification_agent"

    def __init__(self, callback=None, cooldown: float = 30.0):
        self.callback = callback
        self.cooldown = max(0.0, cooldown)
        self._last: dict[str, float] = {}

    def notify(self, event: dict) -> bool:
        key = f"{event.get('camera_name')}:{event.get('event_type')}"
        now = time.monotonic()
        if now - self._last.get(key, 0.0) < self.cooldown:
            return False
        self._last[key] = now
        if self.callback:
            self.callback(dict(event))
        return True
