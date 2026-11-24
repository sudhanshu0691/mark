"""Strict single-unknown-person alert state machine."""
from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(frozen=True)
class Detection:
    identity: str | None
    reliable: bool = True


class UnknownMonitor:
    def __init__(self, duration: float = 10.0, cooldown: float = 300.0, clock=time.monotonic):
        self.duration = float(duration)
        self.cooldown = float(cooldown)
        self._clock = clock
        self._started: float | None = None
        self._last_alert = float("-inf")

    @property
    def pending_for(self) -> float:
        return 0.0 if self._started is None else max(0.0, self._clock() - self._started)

    def reset(self) -> None:
        self._started = None

    def update(self, detections: list[Detection]) -> bool:
        """Return True exactly once when an alert should be sent."""
        now = self._clock()
        if len(detections) != 1 or not detections[0].reliable or detections[0].identity is not None:
            self.reset()
            return False
        if self._started is None:
            self._started = now
            return False
        if now - self._started < self.duration or now - self._last_alert < self.cooldown:
            return False
        self._last_alert = now
        self.reset()
        return True
