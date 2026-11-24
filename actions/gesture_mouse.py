"""Opt-in hand gesture mouse; receives frames from the shared camera monitor."""
from __future__ import annotations

import math
import os
import queue
import threading
import time
from dataclasses import dataclass


@dataclass
class GestureConfig:
    sensitivity: float = 1.0
    smoothing: float = 0.25
    pinch_threshold: float = 0.32  # relative to palm width
    release_threshold: float = 0.46
    debounce_seconds: float = 0.20
    drag_hold_seconds: float = 0.8
    scroll_step: float = 0.035
    fist_hold_seconds: float = 0.8
    fist_min_frames: int = 3


class GestureMouse:
    def __init__(self, status=lambda text: None, config=None, mouse=None, clock=time.monotonic):
        self.status = status
        self.config = config or GestureConfig(
            sensitivity=float(os.getenv("MARK_GESTURE_SENSITIVITY", "1.0")),
            smoothing=float(os.getenv("MARK_GESTURE_SMOOTHING", "0.25")),
            pinch_threshold=float(os.getenv("MARK_GESTURE_PINCH", "0.32")),
            release_threshold=float(os.getenv("MARK_GESTURE_RELEASE", "0.46")),
            debounce_seconds=float(os.getenv("MARK_GESTURE_DEBOUNCE", "0.20")),
            drag_hold_seconds=float(os.getenv("MARK_GESTURE_DRAG_HOLD", "0.8")),
            fist_hold_seconds=max(0.5, float(os.getenv("MARK_GESTURE_FIST_HOLD", "0.8"))))
        self.mouse = mouse
        self.clock = clock
        self._lock = threading.RLock()
        self._queue = queue.Queue(maxsize=1)
        self._enabled = False
        self._thread = None
        self._hands = None
        self._generation = 0
        self.drag_mode = False
        self._reset()

    def _reset(self):
        self._pinch = None
        self._since = 0.0
        self._last_click = 0.0
        self._dragging = False
        self._cursor = None
        self._scroll_y = None
        self._last_seen = None
        self._pose = None
        self._pose_count = 0
        self._fist_since = None

    def enable(self):
        with self._lock:
            if self._enabled:
                return
            try:
                import mediapipe as mp
                import cv2
                if self.mouse is None:
                    import pyautogui
                    self.mouse = pyautogui
                hands = mp.solutions.hands.Hands(max_num_hands=1, min_detection_confidence=0.75,
                                                 min_tracking_confidence=0.7)
            except Exception as exc:
                raise RuntimeError('Gesture tracking unavailable; install requirements-gesture.txt') from exc
            self._hands = hands
            self._reset()
            self._generation += 1
            generation = self._generation
            self._enabled = True
            self._thread = threading.Thread(target=self._run, args=(generation,), daemon=True, name='MarkGestureMouse')
            self._thread.start()
            self.status('STARTING')

    def disable(self, reason='OFF'):
        with self._lock:
            self._enabled = False
            self._generation += 1
            self._release()
            self.drag_mode = False
            self._reset()
            self.status(reason)

    def submit(self, frame):
        with self._lock:
            if not self._enabled:
                return
            try:
                self._queue.put_nowait((self._generation, frame.copy()))
            except queue.Full:
                pass

    def _release(self):
        if self._dragging:
            try:
                self.mouse.mouseUp(button='left')
            finally:
                self._dragging = False

    def _run(self, generation):
        import cv2
        hands = self._hands
        try:
            while True:
                with self._lock:
                    if not self._enabled or generation != self._generation:
                        break
                try:
                    item_generation, frame = self._queue.get(timeout=0.15)
                except queue.Empty:
                    with self._lock:
                        if self._last_seen and self.clock() - self._last_seen > 0.5:
                            self._release()
                            self._pinch = None
                            self._last_seen = None
                            self._fist_since = None
                            self._pose = None
                            self._pose_count = 0
                            self.status('PAUSED: NO HAND')
                    continue
                if item_generation != generation:
                    continue
                rgb = cv2.cvtColor(cv2.flip(frame, 1), cv2.COLOR_BGR2RGB)
                result = hands.process(rgb)
                points = result.multi_hand_landmarks
                with self._lock:
                    if not self._enabled or generation != self._generation:
                        break
                    if not points:
                        self._release()
                        self._pinch = None
                        self._scroll_y = None
                        self._cursor = None
                        self._fist_since = None
                        self._pose = None
                        self._pose_count = 0
                        self.status('PAUSED: NO HAND')
                        continue
                    self._last_seen = self.clock()
                    self.process(points[0].landmark)
        except Exception:
            with self._lock:
                if generation == self._generation:
                    self.disable('OFF: TRACKING ERROR')
        finally:
            hands.close()

    @staticmethod
    def _distance(a, b):
        return math.hypot(a.x - b.x, a.y - b.y)

    def process(self, p):
        """Process landmarks while holding the control lock; also usable in tests."""
        with self._lock:
            if not self._enabled:
                return
            now = self.clock()
            wrist = p[0]
            raised = [self._distance(p[t], wrist) > self._distance(p[j], wrist) * 1.12
                      for t, j in ((8, 6), (12, 10), (16, 14), (20, 18))]
            width = max(self._distance(p[5], p[17]), 0.001)
            # A closed fist has curled fingertips and a thumb tucked near the palm.
            # A single low-confidence frame only pauses; it cannot disable control.
            curled = all(self._distance(p[tip], p[base]) < width * 1.1
                         for tip, base in ((8, 5), (12, 9), (16, 13), (20, 17)))
            tucked_thumb = self._distance(p[4], p[9]) < width * 0.95
            pose = ('fist' if curled and tucked_thumb else 'uncertain') if not any(raised) else ('palm' if all(raised) else 'other')
            self._pose_count = self._pose_count + 1 if pose == self._pose else 1
            self._pose = pose
            if pose != 'other':
                self._release()
                self._pinch = None
                self._scroll_y = None
                self._cursor = None
                if pose == 'fist':
                    if self._fist_since is None:
                        self._fist_since = now
                    if (self._pose_count >= self.config.fist_min_frames and
                            now - self._fist_since >= self.config.fist_hold_seconds):
                        self.disable('OFF: EMERGENCY STOP')
                    else:
                        self.status('PAUSED: HOLD FIST TO STOP')
                else:
                    self._fist_since = None
                    self.status('PAUSED: OPEN PALM' if pose == 'palm' else 'PAUSED: UNCLEAR HAND')
                return
            self._fist_since = None
            self.status('ACTIVE')
            left = self._distance(p[4], p[8]) / width
            right = self._distance(p[4], p[12]) / width
            threshold = self.config.release_threshold if self._pinch else self.config.pinch_threshold
            kind = ('left' if left < right else 'right') if min(left, right) < threshold else None
            if self._pinch and kind != self._pinch:
                old = self._pinch
                if self._dragging:
                    self._release()
                elif kind is None and now - self._since >= self.config.debounce_seconds and now - self._last_click >= self.config.debounce_seconds:
                    self.mouse.click(button=old)
                    self._last_click = now
                self._pinch = None
            if kind and not self._pinch:
                self._pinch, self._since = kind, now
            if self._pinch == 'left' and self.drag_mode and not self._dragging and now - self._since >= self.config.drag_hold_seconds:
                self.mouse.mouseDown(button='left')
                self._dragging = True
            if raised[0] and raised[1] and not raised[2] and not raised[3] and not kind:
                y = (p[8].y + p[12].y) / 2
                if self._scroll_y is not None and abs(y - self._scroll_y) >= self.config.scroll_step:
                    self.mouse.scroll(1 if y < self._scroll_y else -1)
                    self._scroll_y = y
                elif self._scroll_y is None:
                    self._scroll_y = y
                return
            self._scroll_y = None
            if not raised[0] and not self._dragging:
                return
            size = self.mouse.size()
            sensitivity = max(0.2, min(self.config.sensitivity, 3.0))
            x = max(0, min(0.999, 0.5 + (p[8].x - 0.5) * sensitivity)) * (size[0] - 1)
            y = max(0, min(0.999, 0.5 + (p[8].y - 0.5) * sensitivity)) * (size[1] - 1)
            a = max(0.01, min(self.config.smoothing, 1.0))
            self._cursor = (x, y) if self._cursor is None else (self._cursor[0] + a * (x - self._cursor[0]), self._cursor[1] + a * (y - self._cursor[1]))
            self.mouse.moveTo(int(self._cursor[0]), int(self._cursor[1]))
