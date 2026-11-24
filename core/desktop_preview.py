"""Opt-in, low-rate desktop preview over identity-scoped LiveKit data.

No disk persistence, no automatic resume on reconnect, no cloud LLM upload.
Frames are independent JPEGs small enough for a lossy data packet.
"""
import asyncio
import io
import logging
import time

logger = logging.getLogger(__name__)
MAX_PACKET = 1100

def encode_preview(image):
    if image is None:
        return None
    image = image.convert("RGB")
    for width, quality in ((320, 35), (240, 25), (160, 20), (96, 15), (64, 10)):
        frame = image.copy()
        frame.thumbnail((width, width * 9 // 16))
        buffer = io.BytesIO()
        frame.save(buffer, format="JPEG", quality=quality, optimize=True)
        payload = buffer.getvalue()
        if len(payload) <= MAX_PACKET:
            return payload
    return None

class DesktopPreview:
    def __init__(self, room, identity, frame_provider, interval=1.0):
        if not identity:
            raise ValueError("Authorized remote identity required")
        self.room = room
        self.identity = identity
        self.frame_provider = frame_provider
        self.interval = max(0.5, float(interval))
        self._task = None

    @property
    def running(self):
        return self._task is not None and not self._task.done()

    def start(self):
        if not self.running:
            self._task = asyncio.create_task(self._run())

    async def stop(self):
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _run(self):
        try:
            while True:
                start = time.monotonic()
                # Never capture/publish when the permitted recipient is absent.
                if self.identity in self.room.remote_participants:
                    frame = self.frame_provider()
                    payload = await asyncio.to_thread(encode_preview, frame)
                    if payload and self.identity in self.room.remote_participants:
                        await self.room.local_participant.publish_data(
                            payload, reliable=False, topic="mark.desktop.preview.jpeg",
                            destination_identities=[self.identity])
                await asyncio.sleep(max(0.05, self.interval - (time.monotonic() - start)))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Desktop preview stopped: %s", type(exc).__name__)
