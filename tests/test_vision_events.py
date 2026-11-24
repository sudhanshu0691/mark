from datetime import datetime, timezone

from vision_department.agents.notification_agent import VisionNotificationAgent
from vision_department.events.event_manager import VisionEventManager
from vision_department.events.event_store import EventStore


def test_events_persist_even_when_notifications_are_cooled_down(tmp_path):
    saved_notifications = []
    store = EventStore(tmp_path / "events.db")
    manager = VisionEventManager(store, on_event=lambda event: saved_notifications.append(event))
    notification = VisionNotificationAgent(lambda event: None, cooldown=60)
    event = {"event_type": "person_detected", "camera_name": "Qubo", "timestamp": "2026-01-01T00:00:00+00:00"}
    assert manager.publish(dict(event))
    assert manager.publish(dict(event))
    assert store.count() == 2
    assert len(saved_notifications) == 2
    assert notification.notify(event)
    assert not notification.notify(event)


def test_history_is_newest_first_and_retention_prunes(tmp_path):
    store = EventStore(tmp_path / "events.db")
    store.add({"event_type": "person_detected", "camera_name": "Qubo", "timestamp": "2020-01-01T00:00:00+00:00"})
    store.add({"event_type": "motion_detected", "camera_name": "Qubo", "timestamp": datetime.now(timezone.utc).isoformat()})
    assert store.history()[0]["event_type"] == "motion_detected"
    assert store.prune(30) == 1


def test_event_history_survives_store_reopen(tmp_path):
    path = tmp_path / "restart.db"
    EventStore(path).add({"event_type": "person_detected", "camera_name": "Qubo",
                          "timestamp": datetime.now(timezone.utc).isoformat()})
    reopened = EventStore(path)
    assert reopened.count() == 1
    assert reopened.history(1)[0]["camera_name"] == "Qubo"

