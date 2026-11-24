"""Vision monitoring panel embedded in MARK's existing PySide dashboard."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QListWidget, QPushButton, QVBoxLayout, QWidget


class VisionDashboard(QWidget):
    frame_ready = Signal(bytes, object)
    status_ready = Signal(str)
    event_ready = Signal(object)
    alert_ready = Signal(object)
    announcement_ready = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.department = None
        self._build()
        self.frame_ready.connect(self._show_frame)
        self.status_ready.connect(self._show_status)
        self.event_ready.connect(self._show_event)
        self.alert_ready.connect(self._show_alert)
        self.announcement_ready.connect(self._show_announcement)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(2000)

    def _build(self):
        root = QVBoxLayout(self)
        header = QHBoxLayout()
        self.status_label = QLabel("VISION STANDBY")
        self.status_label.setObjectName("state")
        self.start_button = QPushButton("Start Monitoring")
        self.start_button.setObjectName("primary")
        self.start_button.clicked.connect(self.start)
        self.stop_button = QPushButton("Stop Monitoring")
        self.stop_button.setObjectName("danger")
        self.stop_button.clicked.connect(self.stop)
        header.addWidget(self.status_label)
        header.addStretch(1)
        header.addWidget(self.start_button)
        header.addWidget(self.stop_button)
        root.addLayout(header)

        cards = QHBoxLayout()
        self.model_label = QLabel("Model: not loaded")
        self.detection_label = QLabel("People events: 0 · Frames: 0")
        self.last_label = QLabel("Last detection: —")
        self.agent_label = QLabel("Agent: ready when started")
        for label in (self.model_label, self.detection_label, self.last_label, self.agent_label):
            label.setFrameShape(QFrame.Shape.StyledPanel)
            label.setMinimumHeight(44)
            cards.addWidget(label, 1)
        root.addLayout(cards)
        self.preview = QLabel("Live camera view is off")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumHeight(300)
        self.preview.setStyleSheet("background:#07101F;border:1px solid #26364D;border-radius:8px;color:#94A3B8")
        root.addWidget(self.preview, 1)
        self.history = QListWidget()
        self.history.setMaximumHeight(150)
        self.history.setAccessibleName("Vision detection event history")
        root.addWidget(QLabel("RECENT EVENTS"))
        root.addWidget(self.history)
        self.hint = QLabel("Cloud stream uses the saved Qubo client session. Recording is disabled.")
        self.hint.setObjectName("muted")
        root.addWidget(self.hint)
        self.stop_button.setEnabled(False)

    def start(self):
        try:
            if self.department is None:
                from .runtime import get_department
                self.department = get_department(on_frame=lambda image, detections: self.frame_ready.emit(image, detections),
                                                 on_status=self.status_ready.emit,
                                                 on_detection_event=self.event_ready.emit,
                                                 on_announcement=self.announcement_ready.emit)
                for event in reversed(self.department.history(25)):
                    self._show_event(event)
            self.department.start()
            self.hint.setText("Monitoring requested. If authentication, model, or device setup is missing, the status will show the failure.")
            self.start_button.setEnabled(False)
            self.stop_button.setEnabled(True)
            self.refresh()
        except Exception as exc:
            self._show_status(f"Unable to start: {type(exc).__name__}: {exc}")

    def stop(self):
        if self.department:
            self.department.stop()
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.preview.setPixmap(QPixmap())
        self.preview.setText("Live camera view is off")
        self.status_label.setText("VISION STOPPED")

    def refresh(self):
        if not self.department:
            return
        try:
            status = self.department.status
            self.detection_label.setText(
                f"People events: {status['detections_total']} · Frames: {status['frames_received']}"
            )
            self.model_label.setText(f"Model: {status['model']}")
            self.last_label.setText(f"Last detection: {status['last_detection'] or '—'}")
            self.agent_label.setText(f"Agent: {'monitoring' if status['monitoring'] else 'standby'}")
            if status["frames_received"] and not status["detections_total"]:
                self.hint.setText(
                    f"Stream is live ({status['frames_received']} frames received), "
                    "but no person has been detected yet."
                )
            self.start_button.setEnabled(not status["monitoring"])
            self.stop_button.setEnabled(status["monitoring"])
        except Exception:
            pass

    def _show_frame(self, image: bytes, detections):
        pixmap = QPixmap()
        if pixmap.loadFromData(image):
            self.preview.setPixmap(pixmap.scaled(self.preview.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                                 Qt.TransformationMode.SmoothTransformation))

    def _show_status(self, value: str):
        self.status_label.setText(f"VISION · {value.upper()}")
        if value.startswith("Disconnected") or value.startswith("Unable to start"):
            self.hint.setText(value)
        self.refresh()

    def _show_event(self, event: dict):
        self.history.insertItem(0, f"{event.get('timestamp', '')} · {event.get('object_class', event.get('event_type'))} · {event.get('confidence', 0):.2f}")
        while self.history.count() > 100:
            self.history.takeItem(self.history.count() - 1)
        self.refresh()

    def _show_alert(self, event: dict):
        self.hint.setText(f"MARK alert · {event.get('object_class', event.get('event_type'))} · {event.get('timestamp', '')}")

    def _show_announcement(self, message: str):
        self.hint.setText(message)
        callback = getattr(self.window(), "announce_vision", None)
        if callback is not None:
            callback(message)

    def shutdown(self):
        if self.department:
            self.department.stop()
