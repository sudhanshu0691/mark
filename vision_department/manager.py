"""Lifecycle coordinator for MARK's Vision Department."""
from __future__ import annotations

import cv2
import time
from datetime import datetime

from actions.identity_manager import IdentityManager
from .agents.notification_agent import VisionNotificationAgent
from .agents.qubo_camera_agent import QuboCameraAgent
from .camera.stream_manager import StreamManager
from .config.settings import ROOT, VisionSettings
from .detection.human_detector import HumanDetector
from .detection.motion_detector import MotionDetector
from .events.event_manager import VisionEventManager
from .events.event_store import EventStore


class VisionDepartment:
    name = "vision"

    def __init__(self, settings: VisionSettings | None = None, on_frame=None, on_status=None, on_event=None, on_detection_event=None, on_announcement=None):
        self.settings = settings or VisionSettings.load()
        self.model = HumanDetector(self.settings.model_path, self.settings.confidence, self.settings.device, self.settings.target_class)
        self.motion = MotionDetector(self.settings.motion_threshold, self.settings.motion_min_area)
        self.store = EventStore(ROOT / "data" / "vision_events.db")
        self.notification = VisionNotificationAgent(on_event, self.settings.alert_cooldown_seconds)
        self.events = VisionEventManager(self.store, self._on_detection, 0.0)
        self.camera = QuboCameraAgent()
        self._on_frame_callback = on_frame
        self._on_status_callback = on_status
        self._on_detection_callback = on_detection_event
        self._on_announcement = on_announcement
        self.identities = IdentityManager()
        self.face_detector = self._create_face_detector()
        self._presence: dict[str, dict[str, float | bool]] = {}
        self._unknown_last_announced = 0.0
        self.stream: StreamManager | None = None
        self.last_detection: str | None = None
        self.last_error: str = ""
        self.frames_received = 0
        self.last_frame_at: str | None = None
        self.last_detection = self.store.latest_timestamp("person_detected")
        self.store.prune(self.settings.retention_days)
        self._last_prune = time.monotonic()

    @property
    def status(self) -> dict:
        return {"department": self.name, "monitoring": bool(self.stream and self.stream._thread and self.stream._thread.is_alive()),
                "camera": self.settings.camera_name, "model": self.model.status,
                "model_path": str(self.settings.model_path), "classes": self.model.names,
                "detections_total": self.store.count("person_detected"),
                "last_detection": self.last_detection, "last_error": self.last_error,
                "frames_received": self.frames_received, "last_frame_at": self.last_frame_at,
                "recording_enabled": self.settings.recording_enabled}

    def start(self) -> None:
        if not self.settings.enabled:
            raise RuntimeError("Vision detection is disabled in model_config.yaml")
        if not self.settings.camera_enabled:
            raise RuntimeError("Camera integration is disabled in model_config.yaml")
        if self.stream and self.stream._thread and self.stream._thread.is_alive():
            return
        # Validate model before the camera/cloud connection is attempted.
        self.model.load()
        connector = self.camera.connector(self.settings)
        self.stream = StreamManager(connector, self._process_frame, self._set_status, self.settings.interval_seconds)
        self.stream.start()

    def stop(self) -> None:
        if self.stream:
            self.stream.stop()
            self.stream = None

    def history(self, limit: int = 100):
        return self.store.history(limit)

    def set_callbacks(self, *, on_frame=None, on_status=None, on_event=None, on_detection_event=None, on_announcement=None) -> None:
        if on_frame is not None:
            self._on_frame_callback = on_frame
        if on_status is not None:
            self._on_status_callback = on_status
        if on_event is not None:
            self.notification.callback = on_event
        if on_detection_event is not None:
            self._on_detection_callback = on_detection_event
        if on_announcement is not None:
            self._on_announcement = on_announcement

    def _set_status(self, status: str) -> None:
        if status.startswith("Disconnected") or status in {"OpenCV is not installed"}:
            self.last_error = status
        elif status == "Connected":
            self.last_error = ""
        if self._on_status_callback:
            self._on_status_callback(status)

    def _process_frame(self, frame) -> None:
        frame = cv2.flip(frame, 1)
        self.frames_received += 1
        self.last_frame_at = datetime.now().astimezone().isoformat(timespec="seconds")
        if time.monotonic() - self._last_prune > 86400:
            self.store.prune(self.settings.retention_days)
            self._last_prune = time.monotonic()
        detections = self.model.detect(frame, self.settings.camera_name)
        self._update_presence(frame, bool(detections))
        motion = self.motion.detect(frame)
        for event in detections:
            self.last_detection = event["timestamp"]
            self.events.publish(event)
        if motion:
            motion["camera_name"] = self.settings.camera_name
            self.events.publish(motion)
        for event in detections:
            box = event["bounding_box"]
            cv2.rectangle(frame, (box["x1"], box["y1"]), (box["x2"], box["y2"]), (34, 211, 238), 2)
            caption = f"{event['object_class']} {event['confidence']:.2f}"
            cv2.putText(frame, caption, (box["x1"], max(20, box["y1"] - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (34, 211, 238), 2)
        if self._on_frame_callback:
            ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 82])
            if ok:
                self._on_frame_callback(encoded.tobytes(), detections)

    @staticmethod
    def _create_face_detector():
        cascade_path = getattr(getattr(cv2, "data", None), "haarcascades", "")
        detector = cv2.CascadeClassifier(cascade_path + "haarcascade_frontalface_default.xml")
        return None if detector.empty() else detector

    def _update_presence(self, frame, person_detected: bool) -> None:
        if self.face_detector is None:
            return
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        boxes = self.face_detector.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(40, 40))
        now = time.monotonic()
        visible: set[str] = set()
        unknown = False
        for x, y, w, h in boxes:
            match = self.identities.match(self.identities.embedding(frame[y:y + h, x:x + w]))
            if match.reliable and match.name:
                name = match.name.replace("_", " ")
                visible.add(name)
                state = self._presence.setdefault(name, {"present": False, "first_seen": now, "standing_announced": False})
                if not state["present"]:
                    state["present"] = True
                    state["first_seen"] = now
                    state["standing_announced"] = False
                    self._announce(f"{name} gate par aa gaya hai.")
                elif not state["standing_announced"] and now - float(state["first_seen"]) >= 15:
                    state["standing_announced"] = True
                    self._announce(f"{name} abhi bhi gate par khade hain.")
            else:
                unknown = True
        for name, state in self._presence.items():
            if state["present"] and name not in visible and now - float(state.get("first_seen", now)) > 3:
                state["present"] = False
        if (unknown or (person_detected and not visible)) and now - self._unknown_last_announced >= 30:
            self._unknown_last_announced = now
            self._announce("Unknown person gate par aaya hai.")

    def _announce(self, message: str) -> None:
        if self._on_announcement:
            self._on_announcement(message)

    def _on_detection(self, event: dict) -> None:
        # Persist and show every detection; cooldown gates only the MARK alert callback.
        if self._on_detection_callback:
            self._on_detection_callback(dict(event))
        self.notification.notify(event)
