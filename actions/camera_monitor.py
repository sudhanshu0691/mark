"""Opt-in continuous camera capture and local identity analysis."""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Callable

import numpy as np

from actions.identity_manager import IdentityManager, Match
from actions.unknown_monitor import Detection, UnknownMonitor

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None


@dataclass(frozen=True)
class CameraStatus:
    connected: bool
    faces: int
    names: tuple[str, ...]
    unknown: bool
    error: str = ""
    pending_seconds: float = 0.0
    alert_due: bool = False


class CameraMonitor:
    def __init__(self, on_frame: Callable[[bytes, CameraStatus], None] | None = None,
                 on_alert: Callable[[bytes], None] | None = None):
        self.on_frame = on_frame
        self.on_alert = on_alert
        self.identities = IdentityManager(threshold=float(os.getenv("MARK_FACE_THRESHOLD", "0.78")))
        self.unknowns = UnknownMonitor(
            duration=float(os.getenv("MARK_UNKNOWN_DURATION", "10")),
            cooldown=float(os.getenv("MARK_ALERT_COOLDOWN", "300")),
        )
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if cv2 is None:
            raise RuntimeError("opencv-python is required for camera monitoring")
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="MarkCameraMonitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        index = int(os.getenv("MARK_CAMERA_INDEX", "0"))
        cap = None
        try:
            cap = cv2.VideoCapture(index, cv2.CAP_DSHOW if os.name == "nt" else cv2.CAP_ANY)
            if not cap.isOpened():
                self._emit(b"", CameraStatus(False, 0, (), False, f"camera index {index} could not be opened"))
                return
            classifier = self._face_detector()
            while not self._stop.wait(0.1):
                ok, frame = cap.read()
                if not ok or frame is None:
                    self.unknowns.reset()
                    self._emit(b"", CameraStatus(False, 0, (), False, "camera returned no frame"))
                    continue
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                boxes = classifier.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6, minSize=(60, 60))
                matches: list[Match] = []
                for x, y, w, h in boxes:
                    matches.append(self.identities.match(self.identities.embedding(frame[y:y + h, x:x + w])))
                detections = [Detection(match.name, match.reliable) for match in matches]
                self._emit_frame(frame, detections)
        except Exception as exc:
            self.unknowns.reset()
            self._emit(b"", CameraStatus(False, 0, (), False, f"camera analysis failed: {type(exc).__name__}: {exc}"))
        finally:
            if cap is not None:
                cap.release()

    @staticmethod
    def _face_detector():
        """Create a detector only when this OpenCV build exposes one."""
        detector_type = getattr(cv2, "CascadeClassifier", None)
        if detector_type is None:
            raise RuntimeError(
                "this OpenCV build does not include CascadeClassifier; "
                "install a standard opencv-python build (not a minimal runtime)"
            )
        cascade_path = getattr(getattr(cv2, "data", None), "haarcascades", "")
        if not cascade_path:
            raise RuntimeError("OpenCV haar-cascade data path is unavailable")
        detector = detector_type(cascade_path + "haarcascade_frontalface_default.xml")
        if detector.empty():
            raise RuntimeError("OpenCV face cascade could not be loaded")
        return detector

    def _emit_frame(self, frame: np.ndarray, detections: list[Detection]) -> None:
        _, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 82])
        names = tuple(d.identity for d in detections if d.identity)
        status = CameraStatus(True, len(detections), names, len(detections) == 1 and detections[0].identity is None,
                              pending_seconds=self.unknowns.pending_for)
        alert_due = self.unknowns.update(detections)
        status = CameraStatus(status.connected, status.faces, status.names, status.unknown,
                              status.error, self.unknowns.pending_for, alert_due)
        if alert_due and self.on_alert:
            self.on_alert(encoded.tobytes())
        self._emit(encoded.tobytes(), status)

    def _emit(self, frame: bytes, status: CameraStatus) -> None:
        if self.on_frame:
            self.on_frame(frame, status)
