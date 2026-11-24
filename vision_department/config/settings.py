"""Typed configuration with safe defaults and project-relative model paths."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = Path(__file__).with_name("model_config.yaml")


@dataclass(frozen=True)
class VisionSettings:
    model_path: Path = ROOT / "human.pt"
    confidence: float = 0.45
    device: str = "auto"
    interval_seconds: float = 1.0
    target_class: str = "person"
    alert_cooldown_seconds: float = 30.0
    camera_name: str = "Qubo Smart Cam 360 3MP"
    camera_enabled: bool = True
    camera_device_id: str = ""
    camera_provider: str = "qubo_cloud"
    stream_quality: str = "high"
    enabled: bool = True
    recording_enabled: bool = False
    retention_days: int = 30
    motion_threshold: int = 25
    motion_min_area: int = 1200

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> "VisionSettings":
        values: dict[str, Any] = {}
        if path.exists():
            try:
                import yaml
            except ImportError as exc:
                raise RuntimeError("PyYAML is required to read vision_department/config/model_config.yaml") from exc
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if not isinstance(raw, dict):
                raise ValueError("Vision configuration must be a YAML mapping")
            values = raw
        model = values.get("model", {})
        detection = values.get("detection", {})
        camera = values.get("camera", {})
        recording = values.get("recording", {})
        events = values.get("events", {})
        motion = values.get("motion", {})
        path_value = Path(str(model.get("path", "human.pt")))
        if not path_value.is_absolute():
            path_value = ROOT / path_value
        settings = cls(
            model_path=path_value.resolve(),
            confidence=float(model.get("confidence", 0.45)),
            device=str(model.get("device", "auto")),
            interval_seconds=float(detection.get("interval_seconds", 1.0)),
            target_class=str(detection.get("target_class", "person")),
            alert_cooldown_seconds=float(detection.get("alert_cooldown_seconds", 30.0)),
            camera_name=str(camera.get("name", "Qubo Smart Cam 360 3MP")),
            camera_enabled=bool(camera.get("enabled", True)),
            camera_device_id=str(camera.get("device_id", "")),
            camera_provider=str(camera.get("provider", "qubo_cloud")),
            stream_quality=str(camera.get("quality", "high")),
            enabled=bool(detection.get("enabled", True)),
            recording_enabled=bool(recording.get("enabled", False)),
            retention_days=int(events.get("retention_days", 30)),
            motion_threshold=int(motion.get("threshold", 25)),
            motion_min_area=int(motion.get("min_area", 1200)),
        )
        if not 0.0 <= settings.confidence <= 1.0:
            raise ValueError("model.confidence must be between 0 and 1")
        if settings.interval_seconds <= 0 or settings.alert_cooldown_seconds < 0:
            raise ValueError("Detection interval must be positive and cooldown cannot be negative")
        if settings.stream_quality not in {"high", "low"}:
            raise ValueError("camera.quality must be 'high' or 'low'")
        if settings.recording_enabled:
            raise ValueError("Recording is not implemented; keep recording.enabled false")
        return settings
