import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from PIL import Image
from core.desktop_preview import DesktopPreview, encode_preview, MAX_PACKET
from core._livekit_worker import LiveKitAgentWorker

def test_preview_packet_is_bounded():
    packet = encode_preview(Image.new("RGB", (1920,1080), "blue"))
    assert packet.startswith(b"\xff\xd8")
    assert len(packet) <= MAX_PACKET
    assert encode_preview(None) is None

def test_preview_targets_owner_and_stops():
    async def run():
        publish = AsyncMock()
        room = SimpleNamespace(remote_participants={"owner":object(),"other":object()}, local_participant=SimpleNamespace(publish_data=publish))
        preview = DesktopPreview(room, "owner", lambda:Image.new("RGB",(640,360),"blue"))
        preview.start()
        for _ in range(100):
            if publish.await_count:
                break
            await asyncio.sleep(.01)
        await preview.stop()
        assert publish.await_count >= 1
        assert publish.await_args.kwargs["destination_identities"] == ["owner"]
        assert publish.await_args.kwargs["reliable"] is False
        assert not preview.running
    asyncio.run(run())

def test_preview_default_is_disabled():
    async def run():
        worker = LiveKitAgentWorker()
        assert (await worker.start_desktop_preview()).startswith("[FAILED]")
    asyncio.run(run())
