"""OpenCV frame-difference motion detection with duplicate-event coalescing."""
from __future__ import annotations

import time
from datetime import datetime, timezone


class MotionDetector:
    def __init__(self, threshold: int = 25, min_area: int = 1200, cooldown: float = 3.0):
        self.threshold = threshold
        self.min_area = min_area
        self.cooldown = cooldown
        self._previous = None
        self._last_event = 0.0

    def detect(self, frame) -> dict | None:
        import cv2
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (21, 21), 0)
        if self._previous is None:
            self._previous = gray
            return None
        delta = cv2.absdiff(self._previous, gray)
        self._previous = gray
        _, mask = cv2.threshold(delta, self.threshold, 255, cv2.THRESH_BINARY)
        mask = cv2.dilate(mask, None, iterations=2)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        boxes = [cv2.boundingRect(contour) for contour in contours if cv2.contourArea(contour) >= self.min_area]
        now = time.monotonic()
        if not boxes or now - self._last_event < self.cooldown:
            return None
        self._last_event = now
        return {"department": "vision", "agent": "motion_detection_agent", "event_type": "motion_detected",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "regions": [{"x": x, "y": y, "width": w, "height": h} for x, y, w, h in boxes]}
