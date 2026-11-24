"""Class-aware person inference; class IDs always come from checkpoint names."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .model_loader import class_names, load_model


def _is_person(name: str, target: str) -> bool:
    normalized = name.strip().casefold().replace("_", " ")
    wanted = target.strip().casefold().replace("_", " ")
    aliases = {"person", "human", "people"}
    return normalized == wanted or (wanted in aliases and normalized in aliases)


class HumanDetector:
    def __init__(self, model_path, confidence: float = 0.45, device: str = "auto", target_class: str = "person"):
        self.model_path = model_path
        self.confidence = confidence
        self.device = device
        self.target_class = target_class
        self.model: Any | None = None
        self.names: dict[int, str] = {}
        self.runtime_device = "cpu"

    def load(self) -> None:
        self.model = load_model(self.model_path, self.device)
        self.names = class_names(self.model)
        self.runtime_device = getattr(self.model, "_mark_runtime_device", "cpu")
        if not any(_is_person(name, self.target_class) for name in self.names.values()):
            raise ValueError(f"No person-like target class {self.target_class!r} found in model classes {self.names}")

    @property
    def status(self) -> str:
        return "ready" if self.model is not None else "not_loaded"

    def detect(self, frame, camera_name: str) -> list[dict[str, Any]]:
        if self.model is None:
            self.load()
        result = self.model.predict(frame, conf=self.confidence, device=self.runtime_device,
                                    verbose=False)[0]
        events: list[dict[str, Any]] = []
        timestamp = datetime.now(timezone.utc).isoformat()
        for box in result.boxes:
            class_id = int(box.cls.item())
            class_name = self.names.get(class_id, str(class_id))
            if not _is_person(class_name, self.target_class):
                continue
            coords = [int(round(float(value))) for value in box.xyxy[0].tolist()]
            events.append({
                "department": "vision", "agent": "human_detection_agent", "event_type": "person_detected",
                "camera_name": camera_name, "object_class": class_name, "class_index": class_id,
                "confidence": float(box.conf.item()), "timestamp": timestamp,
                "bounding_box": {"x1": coords[0], "y1": coords[1], "x2": coords[2], "y2": coords[3]},
            })
        return events
