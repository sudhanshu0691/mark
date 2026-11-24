"""One webcam capture shared by independently controlled consumers."""
from __future__ import annotations
import os
import threading
import time


class CameraSource:
    def __init__(self):
        self._lock = threading.RLock()
        self._listeners = {}
        self._thread = None
        self._last = None
        self._error = None
        self._ready = threading.Event()

    def subscribe(self, key, on_frame, on_error):
        with self._lock:
            self._listeners[key] = (on_frame, on_error)
            if self._thread is None:
                self._ready.clear()
                self._last = self._error = None
                self._thread = threading.Thread(target=self._run, name='MarkWebcam', daemon=True)
                self._thread.start()

    def unsubscribe(self, key):
        with self._lock:
            self._listeners.pop(key, None)

    def snapshot(self, timeout=5):
        token = object()
        self.subscribe(token, lambda frame: None, lambda error: None)
        try:
            if not self._ready.wait(timeout):
                raise RuntimeError('Camera did not provide a frame in time')
            with self._lock:
                if self._last is None:
                    raise RuntimeError(self._error or 'Camera unavailable')
                return self._last.copy()
        finally:
            self.unsubscribe(token)

    def _run(self):
        cap = None
        try:
            import cv2
            index = int(os.getenv('MARK_CAMERA_INDEX', '0'))
            cap = cv2.VideoCapture(index, cv2.CAP_DSHOW if os.name == 'nt' else cv2.CAP_ANY)
            if not cap.isOpened():
                raise RuntimeError(f'camera index {index} could not be opened')
            while True:
                with self._lock:
                    if not self._listeners:
                        break
                ok, frame = cap.read()
                if not ok or frame is None:
                    raise RuntimeError('camera returned no frame')
                with self._lock:
                    self._last = frame.copy()
                    listeners = list(self._listeners.values())
                    self._ready.set()
                for callback, _ in listeners:
                    try:
                        callback(frame)
                    except Exception:
                        pass
                time.sleep(0.03)
        except Exception as exc:
            with self._lock:
                self._last = None
                self._error = f'{type(exc).__name__}: {exc}'
                listeners = list(self._listeners.values())
                self._ready.set()
            for _, callback in listeners:
                try:
                    callback(self._error)
                except Exception:
                    pass
        finally:
            if cap is not None:
                cap.release()
            with self._lock:
                self._thread = None
                self._last = None
                # A subscriber may have arrived as the old thread was closing.
                if self._listeners and self._error is None:
                    self._ready.clear()
                    self._thread = threading.Thread(target=self._run, name='MarkWebcam', daemon=True)
                    self._thread.start()


CAMERA = CameraSource()
