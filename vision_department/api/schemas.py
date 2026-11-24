"""API response schemas."""
from pydantic import BaseModel


class VisionStatus(BaseModel):
    department: str
    monitoring: bool
    camera: str
    model: str
    model_path: str
    classes: dict[int, str]
    detections_total: int
    last_detection: str | None
    last_error: str
    recording_enabled: bool
