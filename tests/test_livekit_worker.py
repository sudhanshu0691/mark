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
            self.local_participant = SimpleNamespace(publish_track=AsyncMock(),
                                                     set_track_subscription_permissions=lambda **kwargs: None)
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
            TrackKind=SimpleNamespace(KIND_AUDIO=1), ParticipantTrackPermission=SimpleNamespace)
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


def test_voice_endpointing_keeps_start_of_speech_and_discards_single_noise_peak():
    from core._vad import VoiceActivityDetector

    def frame(amplitude):
        return amplitude.to_bytes(2, "little", signed=True) * 320

    detector = VoiceActivityDetector()
    assert detector.feed(frame(2000)) == (False, None)
    for _ in range(6):
        assert detector.feed(frame(90)) == (False, None)
    events = [detector.feed(frame(2000)) for _ in range(12)]
    assert sum(started for started, _ in events) == 1
    assert all(packet is None for _, packet in events)
    packets = [packet for _, packet in (detector.feed(frame(90)) for _ in range(30)) if packet]
    assert len(packets) == 1
    assert frame(2000) in packets[0]
