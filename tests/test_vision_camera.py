import asyncio

import pytest

from vision_department.camera.connection import QuboCameraConnection
from vision_department.camera.stream_manager import StreamManager
from vision_department.agents.qubo_camera_agent import QuboCameraAgent


def test_qubo_connection_requires_explicit_device_id_before_network_access():
    connection = QuboCameraConnection("")
    with pytest.raises(RuntimeError, match="camera.device_id"):
        asyncio.run(connection.open())


def test_stopping_unstarted_stream_manager_is_safe():
    manager = StreamManager(object(), lambda *_: None, lambda *_: None)
    manager.stop()


def test_qubo_agent_auto_selects_the_only_discovered_camera(monkeypatch):
    agent = QuboCameraAgent()
    monkeypatch.setattr(agent, "discover", lambda: [{"device_id": "camera-uuid", "name": "Front Camera", "model": "Qubo"}])
    assert agent.resolve_device_id("") == "camera-uuid"


def test_qubo_agent_requires_configuration_for_multiple_cameras(monkeypatch):
    agent = QuboCameraAgent()
    monkeypatch.setattr(agent, "discover", lambda: [
        {"device_id": "front-id", "name": "Front Camera", "model": "Qubo"},
        {"device_id": "back-id", "name": "Back Camera", "model": "Qubo"},
    ])
    with pytest.raises(RuntimeError, match="Multiple Qubo cameras"):
        agent.resolve_device_id("")


def test_qubo_cloud_connector_uses_device_uuid_and_stops_same_quality(monkeypatch):
    import qubopy
    import vision_department.camera.session as session

    calls = []

    class Device:
        device_id = "camera-uuid"

        async def start_stream(self, quality):
            calls.append(("start", quality))
            return "rtsps://stream.example.invalid/live/token"

        async def stop_stream(self, quality):
            calls.append(("stop", quality))

    class Client:
        def __init__(self, auth):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get_all_devices(self):
            return [Device()]

    monkeypatch.setattr(qubopy, "QuboAuth", lambda: object())
    monkeypatch.setattr(qubopy, "QuboClient", Client)
    monkeypatch.setattr(session, "load_session", lambda auth: True)
    connector = QuboCameraConnection("camera-uuid", "high")
    assert asyncio.run(connector.open()).startswith("rtsps://")
    asyncio.run(connector.close())
    assert calls == [("start", "high"), ("stop", "high")]
