"""Reconnectable capture loop for a connector-provided stream URL."""
from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable


class StreamManager:
    def __init__(self, connector, on_frame: Callable, on_status: Callable, frame_interval: float = 1.0):
        self.connector = connector
        self.on_frame = on_frame
        self.on_status = on_status
        self.frame_interval = max(0.05, frame_interval)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="MARKVisionStream", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=5)

    def _run(self) -> None:
        try:
            import cv2
        except ImportError:
            self.on_status("OpenCV is not installed")
            return
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        capture = None
        retry_delay = 3.0
        try:
            while not self._stop.is_set():
                try:
                    self.on_status("Connecting to Qubo cloud stream…")
                    url = loop.run_until_complete(self.connector.open())
                    capture = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
                    if not capture.isOpened():
                        raise RuntimeError("OpenCV/FFmpeg could not open the authenticated Qubo stream")
                    retry_delay = 3.0
                    self.on_status("Connected")
                    last_frame = 0.0
                    while not self._stop.is_set():
                        ok, frame = capture.read()
                        if not ok or frame is None:
                            raise RuntimeError("Qubo stream ended or returned no frame")
                        now = time.monotonic()
                        if now - last_frame >= self.frame_interval:
                            self.on_frame(frame)
                            last_frame = now
                except Exception as exc:
                    # Never include exception text: third-party errors can echo URL tokens.
                    self.on_status(f"Disconnected ({type(exc).__name__}); retrying")
                    if not self._stop.wait(retry_delay):
                        retry_delay = min(30.0, retry_delay * 2.0)
                        continue
                finally:
                    if capture is not None:
                        capture.release()
                        capture = None
                    try:
                        loop.run_until_complete(self.connector.close())
                    except Exception:
                        pass
        finally:
            loop.close()
            self.on_status("Stopped")
