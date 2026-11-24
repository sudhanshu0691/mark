"""Focused Gemini Live audio transport tests."""
import asyncio

import pytest
from unittest.mock import Mock, AsyncMock

from main import MarkLive, _is_live_audio_configuration_error


@pytest.mark.parametrize("message", [
    "1007 None. The audio content type (CONTENT_TYPE_AUDIO) is not supported for this model configuration.",
    "CONTENT_TYPE_AUDIO is not supported",
    "audio content type is not supported for this model configuration",
])
def test_live_audio_configuration_errors_are_fatal(message):
    assert _is_live_audio_configuration_error(RuntimeError(message))


def test_unrelated_live_errors_can_retry():
    assert not _is_live_audio_configuration_error(RuntimeError("connection timed out"))


def test_realtime_audio_uses_audio_field():
    sent = []

    class Session:
        async def send_realtime_input(self, **kwargs):
            sent.append(kwargs)

    async def run():
        live = MarkLive.__new__(MarkLive)
        live.out_queue = asyncio.Queue()
        live.session = Session()
        await live.out_queue.put({"data": b"\x00\x00", "mime_type": "audio/pcm;rate=16000"})

        task = asyncio.create_task(MarkLive._send_realtime(live))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert sent[0]["audio"].mime_type == "audio/pcm;rate=16000"
    assert "media" not in sent[0]


def test_live_has_no_desktop_stream():
    import inspect
    import main
    assert not hasattr(MarkLive, "_stream_screen")
    assert "send_realtime_input(video=" not in inspect.getsource(main)


def test_live_connect_starts_even_when_microphone_is_muted(monkeypatch):
    import main

    live = MarkLive.__new__(MarkLive)
    live.ui = Mock(muted=True)
    live.plugins = Mock()
    live.screen_monitor = Mock()
    live._push_hud_counts = Mock()
    live._notification_pump = AsyncMock()
    live._on_notification = Mock()
    live._live_disabled = False
    live._live_key_id = None
    live._resume_handle = None
    live._live_in, live._live_out, live._completed_calls = [], [], {}
    live._build_config = Mock(return_value={})
    watcher = Mock()
    watcher.listeners = []
    monkeypatch.setattr(main, "NOTIF_WATCHER", watcher)
    monkeypatch.setattr(main._llm_config, "load_config", lambda: {"profile": "balanced"})
    monkeypatch.setattr(main, "_get_api_key", lambda: "a" * 30)
    client = Mock()
    client.aio.live.connect.side_effect = asyncio.CancelledError
    client.aio.aclose = AsyncMock()
    monkeypatch.setattr(main.genai, "Client", Mock(return_value=client))

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(live._serve())
    client.aio.live.connect.assert_called_once()


def test_inactivity_is_not_misclassified_as_invalid_key():
    from main import _is_live_inactivity_error, _is_invalid_api_key_error
    idle = ExceptionGroup('task group', [RuntimeError('1008 None. Session closed due to client inactivity.')])
    assert _is_live_inactivity_error(idle)
    assert not _is_invalid_api_key_error(idle)
    assert _is_invalid_api_key_error(RuntimeError('API key not valid'))
    assert not _is_live_inactivity_error(RuntimeError('1008 policy violation'))
    assert not _is_invalid_api_key_error(RuntimeError('1008 policy violation'))


def test_receive_inactivity_does_not_print_traceback(capsys):
    live = MarkLive.__new__(MarkLive)
    live._live_in, live._live_out = [], []
    class Socket:
        async def receive(self):
            if False:
                yield None
            raise RuntimeError('1008 None. Session closed due to client inactivity.')
    live.session = Socket()
    with pytest.raises(RuntimeError):
        asyncio.run(live._receive_audio())
    assert capsys.readouterr().out == ''


def test_idle_waits_until_microphone_unmuted():
    from main import _wait_for_microphone
    async def run():
        ui = Mock(muted=True)
        task = asyncio.create_task(_wait_for_microphone(ui))
        await asyncio.sleep(.05)
        assert not task.done()
        ui.muted = False
        await asyncio.wait_for(task, 1)
    asyncio.run(run())
