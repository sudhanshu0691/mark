from vision_department.agents.vision_event_agent import VisionEventManager
from vision_department.config.settings import VisionSettings
from vision_department.manager import VisionDepartment


def test_department_exposes_mark_health_and_history(tmp_path, monkeypatch):
    import vision_department.manager as module

    class Store:
        def prune(self, *_):
            pass

        def count(self, *_):
            return 7

        def add(self, _event):
            return 8

        def history(self, *_):
            return [{"event_type": "person_detected"}]

        def latest_timestamp(self, *_):
            return "2026-01-01T00:00:00+00:00"

    monkeypatch.setattr(module, "EventStore", lambda *_: Store())
    settings = VisionSettings(model_path=tmp_path / "model.pt")
    department = VisionDepartment(settings=settings)
    assert department.status["department"] == "vision"
    assert department.status["detections_total"] == 7
    assert department.history() == [{"event_type": "person_detected"}]
    assert VisionEventManager is not None


def test_root_receives_cooldown_alerts_while_dashboard_keeps_every_detection(tmp_path, monkeypatch):
    import vision_department.manager as module

    class Store:
        def prune(self, *_): pass
        def count(self, *_): return 0
        def add(self, _event): return 1
        def history(self, *_): return []
        def latest_timestamp(self, *_): return None

    monkeypatch.setattr(module, "EventStore", lambda *_: Store())
    alerts, history = [], []
    department = VisionDepartment(settings=VisionSettings(model_path=tmp_path / "model.pt"),
                                  on_event=alerts.append, on_detection_event=history.append)
    event = {"event_type": "person_detected", "camera_name": "Qubo", "object_class": "Human",
             "timestamp": "2026-01-01T00:00:00+00:00"}
    department.events.publish(dict(event))
    department.events.publish(dict(event))
    assert len(history) == 2
    assert len(alerts) == 1
