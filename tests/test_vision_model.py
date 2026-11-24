from pathlib import Path

import pytest

from vision_department.config.settings import ROOT, VisionSettings
from vision_department.detection.human_detector import HumanDetector
from vision_department.detection.model_loader import class_names, load_model


def test_supplied_checkpoint_loads_and_exposes_real_classes():
    checkpoint = ROOT / "assets" / "models" / "human.pt"
    if not checkpoint.exists():
        pytest.skip("supplied project checkpoint is not present")
    model = load_model(checkpoint, "cpu")
    assert class_names(model) == {0: "Human"}


def test_detector_maps_person_alias_to_actual_human_class():
    class Scalar:
        def __init__(self, value):
            self.value = value

        def item(self):
            return self.value

    class Coordinates:
        def tolist(self):
            return [12.2, 20.8, 80.4, 150.1]

    class Box:
        cls = Scalar(0)
        conf = Scalar(0.91)
        xyxy = [Coordinates()]

    class Result:
        boxes = [Box()]

    class FakeModel:
        names = {0: "Human"}

        def predict(self, *args, **kwargs):
            return [Result()]

    detector = HumanDetector(Path("human.pt"))
    detector.model = FakeModel()
    detector.names = {0: "Human"}
    output = detector.detect(object(), "Qubo")
    assert output[0]["class_index"] == 0
    assert output[0]["object_class"] == "Human"
    assert output[0]["bounding_box"] == {"x1": 12, "y1": 21, "x2": 80, "y2": 150}
    assert output[0]["event_type"] == "person_detected"


def test_missing_model_fails_before_inference(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_model(tmp_path / "missing.pt")


def test_config_uses_supplied_model_and_safe_defaults():
    settings = VisionSettings.load()
    assert settings.model_path == (ROOT / "assets" / "models" / "human.pt").resolve()
    assert settings.confidence == 0.45
    assert settings.interval_seconds == 1.0
    assert settings.alert_cooldown_seconds == 30.0
    assert settings.recording_enabled is False


def test_config_resolves_legacy_model_path_after_reorganization(tmp_path):
    config = tmp_path / "vision.yaml"
    config.write_text("model:\n  path: human.pt\n", encoding="utf-8")
    assert VisionSettings.load(config).model_path == VisionSettings().model_path
