"""RTC worker lifecycle tests without external services or audio hardware."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from core._livekit_worker import LiveKitAgentWorker

def test_duplicate_subscription_and_stale_room(monkeypatch):
    import sys
    class Room:
        def __init__(self):
            self.handlers = {}
            self.remote_participants = {}
            self.local_participant = SimpleNamespace(publish_track=AsyncMock())
            self.disconnect = AsyncMock()
        def on(self, event):
            def register(fn):
                self.handlers[event] = fn
                return fn
            return register
    async def run():
        source = SimpleNamespace(clear_queue=lambda: None, aclose=AsyncMock())
        rtc = SimpleNamespace(AudioSource=lambda *a: source,
            LocalAudioTrack=SimpleNamespace(create_audio_track=lambda *a: object()),
            TrackKind=SimpleNamespace(KIND_AUDIO=1))
        monkeypatch.setitem(sys.modules, "livekit", SimpleNamespace(rtc=rtc))
        worker = LiveKitAgentWorker(object(), stt_engine=SimpleNamespace(load=AsyncMock()),
            tts_engine=SimpleNamespace(load=AsyncMock()))
        worker.allowed_identity = "authorized"
        monkeypatch.setattr(worker, "_receive_audio", lambda track: asyncio.sleep(100))
        first, second = Room(), Room()
        await worker.start(first)
        track, publication = SimpleNamespace(kind=1), SimpleNamespace(sid="audio-1")
        callback = first.handlers["track_subscribed"]
        callback(track, publication, SimpleNamespace(identity="unauthorized"))
        assert not worker._receivers
        callback(track, publication, SimpleNamespace(identity="authorized"))
        callback(track, publication, SimpleNamespace(identity="authorized"))
        assert len(worker._receivers) == 1
        await worker.start(second)
        assert not worker._receivers
        first.handlers["disconnected"]()
        assert worker._is_running
        await worker.stop()
        assert worker._room is None
        assert source.aclose.await_count == 2
    asyncio.run(run())


def test_response_data_is_only_sent_to_authorized_identity():
    async def run():
        worker = LiveKitAgentWorker()
        publish = AsyncMock()
        worker._room = SimpleNamespace(local_participant=SimpleNamespace(publish_data=publish))
        worker._is_running = True
        await worker.send_text("private")
        publish.assert_not_awaited()
        worker.allowed_identity = "owner"
        await worker.send_text("private")
        publish.assert_awaited_once_with(b"private", reliable=True, topic="mark.response", destination_identities=["owner"])
    asyncio.run(run())
