from fastapi import FastAPI
from fastapi.testclient import TestClient

from vision_department.api.routes import bind_department, router


class FakeDepartment:
    status = {"department": "vision", "monitoring": False, "camera": "Qubo", "model": "ready",
              "model_path": "human.pt", "classes": {0: "Human"}, "detections_total": 1,
              "last_detection": None, "last_error": "", "recording_enabled": False}

    def history(self, limit=100):
        return [{"event_type": "person_detected"}]

    def start(self):
        pass

    def stop(self):
        pass


def test_api_is_local_and_exposes_status_history_and_controls():
    bind_department(FakeDepartment())
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        assert client.get("/api/vision/status").json()["department"] == "vision"
        assert client.get("/api/vision/events").json()[0]["event_type"] == "person_detected"
        assert client.post("/api/vision/start").status_code == 200
        assert client.post("/api/vision/stop").status_code == 200


def test_api_rejects_non_loopback_clients():
    bind_department(FakeDepartment())
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app, client=("10.0.0.12", 12345))
    assert client.get("/api/vision/status").status_code == 403
