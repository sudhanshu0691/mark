"""Bounded native desktop capture for a LiveKit screen-share track."""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)


class DesktopVideo:
    def __init__(self, room, identity, monitor, resolution, on_active, *, window=None):
        self.room = room
        self.identity = identity
        self.monitor = monitor
        self._window = window
        self.width, self.height = (1920, 1080) if resolution == 1080 else (1280, 720)
        self.on_active = on_active
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="mark-desktop"
        )
        self._capture = None
        self._task = None
        self._stopped = asyncio.Event()
        self._started = asyncio.Event()
        self._published = False

    @property
    def running(self):
        return (
            self._task is not None
            and not self._task.done()
            and not self._stopped.is_set()
        )

    def _capture_frame(self):
        from mss import MSS
        from PIL import Image, ImageOps

        if self._window is not None:
            image = self._window.frame()
        else:
            if self._capture is None:
                self._capture = MSS()
            monitors = self._capture.monitors
            if not 1 <= self.monitor < len(monitors):
                raise ValueError("Selected monitor is unavailable.")
            shot = self._capture.grab(monitors[self.monitor])
            image = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        if image.size != (self.width, self.height):
            image = ImageOps.pad(
                image, (self.width, self.height), Image.Resampling.BILINEAR
            )
        return image.tobytes()

    def _close_capture(self):
        if self._capture is not None:
            self._capture.close()
            self._capture = None

    async def start(self):
        self._task = asyncio.create_task(self._run())
        try:
            await asyncio.wait_for(self._started.wait(), timeout=12)
        except (TimeoutError, asyncio.CancelledError):
            await self.stop()
            raise
        return self._published and self.running

    async def stop(self):
        self._stopped.set()
        if self._window is not None:
            await asyncio.to_thread(self._window.close)
        if self._task is not None:
            await asyncio.shield(self._task)

    def _authorized(self):
        return (
            not self._stopped.is_set()
            and self.identity in self.room.remote_participants
        )

    async def _run(self):
        from livekit import rtc

        loop = asyncio.get_running_loop()
        source = publication = None
        try:
            if not self._authorized():
                return
            frame = await loop.run_in_executor(self._executor, self._capture_frame)
            if not self._authorized():
                return
            source = rtc.VideoSource(self.width, self.height, is_screencast=True)
            track = rtc.LocalVideoTrack.create_video_track("mark-desktop", source)
            options = rtc.TrackPublishOptions(
                source=rtc.TrackSource.SOURCE_SCREENSHARE,
                video_encoding=rtc.VideoEncoding(
                    max_bitrate=6_000_000 if self.height == 1080 else 3_000_000,
                    max_framerate=30,
                ),
                simulcast=True,
            )
            publication = await asyncio.wait_for(
                self.room.local_participant.publish_track(track, options), 8
            )
            if not self._authorized():
                return
            self._published = True
            self.on_active(True)
            self._started.set()
            while self._authorized():
                started = loop.time()
                source.capture_frame(
                    rtc.VideoFrame(
                        self.width, self.height, rtc.VideoBufferType.RGB24, frame
                    )
                )
                # One frame in flight. Slow capture or encoder ingestion reduces FPS
                # rather than accumulating stale desktop frames.
                if self._authorized():
                    frame = await loop.run_in_executor(
                        self._executor, self._capture_frame
                    )
                await asyncio.sleep(max(0, 1 / 30 - (loop.time() - started)))
        except Exception as exc:
            logger.warning("Desktop video stopped: %s", type(exc).__name__)
        finally:
            self._started.set()
            self.on_active(False)
            try:
                if publication is not None:
                    await asyncio.wait_for(
                        self.room.local_participant.unpublish_track(publication.sid), 3
                    )
            except Exception as exc:
                logger.warning("Desktop track cleanup failed: %s", type(exc).__name__)
            finally:
                try:
                    if source is not None:
                        await source.aclose()
                    if self._window is not None:
                        await asyncio.to_thread(self._window.close)
                    await loop.run_in_executor(self._executor, self._close_capture)
                finally:
                    self._executor.shutdown(wait=False, cancel_futures=True)
