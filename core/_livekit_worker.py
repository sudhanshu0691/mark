"""Opt-in LiveKit room transport using MARK's existing secure task runtime."""
from __future__ import annotations

import asyncio
import logging
import re
from uuid import uuid4
from core.voice_stream import SentenceBuffer
from core._vad import VoiceActivityDetector

logger = logging.getLogger(__name__)


class LiveKitAgentWorker:
    def __init__(self, markdown_adapter=None, llm_gateway=None, stt_engine=None, tts_engine=None):
        self.markdown_adapter = markdown_adapter
        self.llm_gateway = llm_gateway
        self.stt_engine = stt_engine
        self.tts_engine = tts_engine
        self._room = None
        self._source = None
        self._is_running = False
        self._is_speaking = False
        self._turn_lock = asyncio.Lock()
        self._tasks = set()
        self._receivers = set()
        self._track_tasks = {}
        self._turn_task = None
        self._pending_text = None
        self._cancel = asyncio.Event()
        self._desktop_preview = None
        self.desktop_preview_allowed = False
        self.desktop_video_allowed = False
        self._desktop_video = None
        self._share_generation = 0
        self._share_lock = asyncio.Lock()

    async def start(self, room, participant=None):
        from livekit import rtc
        if not self.markdown_adapter or not self.stt_engine or not self.tts_engine:
            raise RuntimeError("MARK voice adapters are not configured")
        if self._room is not None:
            await self.stop()
        await self.stt_engine.load()
        await self.tts_engine.load()
        self._room = room
        identity = getattr(self, "allowed_identity", None)
        if not identity:
            raise RuntimeError("LiveKit requires an authorized participant identity.")
        room.local_participant.set_track_subscription_permissions(
            allow_all_participants=False,
            participant_permissions=[rtc.ParticipantTrackPermission(participant_identity=identity, allow_all=True)],
        )
        self._source = rtc.AudioSource(24000, 1)
        track = rtc.LocalAudioTrack.create_audio_track("mark-speech", self._source)
        await room.local_participant.publish_track(track)
        self._is_running = True
        self._cancel.clear()

        @room.on("disconnected")
        def disconnected(*args):
            if self._room is not room:
                return
            self._is_running = False
            self._cancel.set()
            self._pending_text = None
            asyncio.create_task(self.stop_desktop_sharing())
            for task in tuple(self._receivers):
                task.cancel()

        @room.on("reconnecting")
        def reconnecting(*args):
            if self._room is room:
                asyncio.create_task(self.stop_desktop_sharing())

        @room.on("participant_disconnected")
        def participant_left(participant):
            if self._room is room and participant.identity == getattr(self, "allowed_identity", None):
                asyncio.create_task(self.stop_desktop_sharing())

        @room.on("track_subscribed")
        def subscribed(track, publication, remote):
            if self._room is not room or not self._is_running:
                return
            if track.kind != rtc.TrackKind.KIND_AUDIO:
                return
            # Explicitly configured remote identity; never process arbitrary room users.
            allowed = getattr(self, "allowed_identity", None)
            if not allowed or remote.identity != allowed:
                return
            key = getattr(publication, "sid", None) or id(track)
            existing = self._track_tasks.get(key)
            if existing is not None and not existing.done():
                return
            task = asyncio.create_task(self._receive_audio(track))
            self._track_tasks[key] = task
            self._receivers.add(task)
            def finished(done):
                self._receivers.discard(done)
                if self._track_tasks.get(key) is done:
                    self._track_tasks.pop(key, None)
                if not done.cancelled() and done.exception() is not None:
                    logger.warning("Audio receiver failed: %s", type(done.exception()).__name__)
            task.add_done_callback(finished)

        @room.on("track_unsubscribed")
        def unsubscribed(track, publication, remote):
            if self._room is not room:
                return
            key = getattr(publication, "sid", None) or id(track)
            task = self._track_tasks.get(key)
            if task is not None:
                task.cancel()

        for remote in room.remote_participants.values():
            for publication in remote.track_publications.values():
                if publication.track is not None:
                    subscribed(publication.track, publication, remote)

    async def _receive_audio(self, track):
        from livekit import rtc
        stream = rtc.AudioStream(track, sample_rate=16000, num_channels=1)
        detector = VoiceActivityDetector()
        try:
            async for event in stream:
                if not self._is_running:
                    break
                started, utterance = detector.feed(bytes(event.frame.data), playback=self._is_speaking)
                if started and self._turn_task is not None and not self._turn_task.done():
                    await self.interrupt()
                if utterance:
                    text = await self.stt_engine.transcribe(utterance, 16000)
                    if text:
                        self.submit_text(text)
        finally:
            await stream.aclose()

    def submit_text(self, text):
        if not self._is_running:
            return
        if text.strip().casefold().rstrip(".!?") in {
            "stop desktop preview", "stop desktop video", "stop desktop sharing",
        }:
            asyncio.create_task(self.stop_desktop_sharing())
        if self._turn_task is not None and not self._turn_task.done():
            self._pending_text = text
            self._cancel.set()
            if self._source:
                self._source.clear_queue()
            return
        self._turn_task = asyncio.create_task(self.handle_text(text))
        self._tasks.add(self._turn_task)
        self._turn_task.add_done_callback(self._turn_finished)

    def _turn_finished(self, task):
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.error("Voice turn failed: %s", type(task.exception()).__name__)
        if self._turn_task is task:
            self._turn_task = None
            pending, self._pending_text = self._pending_text, None
            if pending and self._is_running:
                self.submit_text(pending)

    async def handle_text(self, text):
        from core.agents.runtime import execute_turn
        from memory import db
        async with self._turn_lock:
            if not self._is_running:
                return
            self._cancel.clear()
            lock = self.markdown_adapter._local_command_lock
            if not lock.acquire(blocking=False):
                await self.speak_text("Another task is running. Please try again.")
                return
            try:
                active = await asyncio.to_thread(db.get_active_conversation)
                conversation_id = active["id"] if active else None
                request_id = uuid4().hex
                await asyncio.to_thread(db.checkpoint_conversation_turn, request_id, text,
                                        channel="voice", conversation_id=conversation_id)
                loop = asyncio.get_running_loop()
                queue = asyncio.Queue(maxsize=64)
                overflow = False
                buffer = SentenceBuffer()
                streamed = False
                def enqueue(value):
                    nonlocal overflow
                    if overflow or self._cancel.is_set():
                        return
                    try:
                        queue.put_nowait(value)
                    except asyncio.QueueFull:
                        # Preserve the final answer in the database; stop incremental
                        # delivery rather than accumulating unlimited queued audio.
                        overflow = True
                def delta(value):
                    if not self._cancel.is_set():
                        loop.call_soon_threadsafe(enqueue, value)
                async def consume():
                    nonlocal streamed
                    while True:
                        value = await queue.get()
                        if value is None:
                            tail = buffer.flush()
                            if tail and not overflow and not self._cancel.is_set():
                                await self.speak_text(tail)
                            return
                        if self._cancel.is_set() or overflow:
                            continue
                        streamed = True
                        await self._publish_text(value, "mark.response.delta")
                        for sentence in buffer.feed(value):
                            if not self._cancel.is_set():
                                await self.speak_text(sentence)
                consumer = asyncio.create_task(consume())
                try:
                    try:
                        command = text.strip().casefold().rstrip(".!?")
                        video = re.fullmatch(r"start desktop video(?: (720|1080)p)?(?: monitor ([1-9][0-9]?))?", command)
                        if command in {"stop desktop preview", "stop desktop video", "stop desktop sharing"}:
                            answer = await self.stop_desktop_sharing()
                        elif video:
                            answer = await self.start_desktop_video(int(video[1] or 720), int(video[2] or 1))
                        elif command == "start desktop preview":
                            answer = await self.start_desktop_preview()
                        else:
                            answer = await asyncio.to_thread(
                                execute_turn, text, dispatch=self.markdown_adapter._dispatch_sync,
                            declarations=self.markdown_adapter._all_declarations,
                            conversation_id=conversation_id, cancelled=self._cancel.is_set, on_delta=delta)
                    except Exception as exc:
                        logger.error("Voice inference failed: %s", type(exc).__name__)
                        answer = "[FAILED] Voice inference failed."
                    head = answer.split("\nEXECUTION TRACE:")[0].strip()
                    if self._cancel.is_set():
                        head = "[CANCELLED] Voice turn interrupted; any tool effects may already exist."
                    # Commit the completed turn before waiting for audio delivery.
                    await asyncio.to_thread(db.checkpoint_conversation_turn, request_id, text, head,
                                            channel="voice", conversation_id=conversation_id, complete=True)
                finally:
                    if not consumer.done():
                        # A failed consumer must not leave a producer blocked on a full queue.
                        sentinel = asyncio.create_task(queue.put(None))
                        await asyncio.wait((sentinel, consumer), return_when=asyncio.FIRST_COMPLETED)
                        if not sentinel.done():
                            sentinel.cancel()
                            await asyncio.gather(sentinel, return_exceptions=True)
                    try:
                        await consumer
                    except Exception as exc:
                        logger.warning("Voice delivery failed: %s", type(exc).__name__)
                if not self._cancel.is_set() and self._is_running:
                    await self.send_text(head)
                    if not streamed:
                        await self.speak_text(head[:1800])
                    elif overflow:
                        await self.speak_text("The full response is available in text.")
            finally:
                lock.release()

    def _sharing_indicator(self, active):
        ui = getattr(self.markdown_adapter, "ui", None)
        if ui is not None:
            ui.set_desktop_sharing(active)

    async def _confirm_share(self, mode, identity, **details):
        ui = getattr(self.markdown_adapter, "ui", None)
        return ui is not None and await ui.confirm_action(
            "share your desktop via LiveKit", {"recipient": identity, "mode": mode, **details})

    async def start_desktop_video(self, resolution, monitor):
        from core.streaming.desktop import DesktopVideo

        async with self._share_lock:
            generation = self._share_generation
            identity = getattr(self, "allowed_identity", None)
            room = self._room
            if (not self.desktop_video_allowed or not self._is_running
                    or not room or identity not in room.remote_participants):
                return "[FAILED] Desktop video is disabled or its authorized recipient is unavailable."
            if self._desktop_video is not None and self._desktop_video.running:
                return "[DONE] Desktop video already active."
            if not await self._confirm_share("video", identity, resolution=f"{resolution}p", monitor=monitor):
                return "[CANCELLED] Desktop sharing was declined."
            if generation != self._share_generation or self._room is not room or not self._is_running:
                return "[CANCELLED] Desktop sharing request expired."
            await self.stop_desktop_preview()
            if generation != self._share_generation:
                return "[CANCELLED] Desktop sharing request expired."
            video = DesktopVideo(room, identity, monitor, resolution, self._sharing_indicator)
            self._desktop_video = video
            try:
                if await video.start():
                    return "[DONE] Desktop video started for the authorized participant."
            except TimeoutError:
                pass
            return "[FAILED] Desktop video could not start."

    async def start_desktop_preview(self):
        from core.desktop_preview import DesktopPreview

        async with self._share_lock:
            generation = self._share_generation
            identity = getattr(self, "allowed_identity", None)
            room = self._room
            if (not self.desktop_preview_allowed or not self._is_running
                    or not room or identity not in room.remote_participants):
                return "[FAILED] Desktop preview is disabled or disconnected."
            monitor = getattr(self.markdown_adapter, "screen_monitor", None)
            if monitor is None:
                return "[FAILED] Local screen monitor unavailable."
            if self._desktop_preview is not None and self._desktop_preview.running:
                return "[DONE] Desktop preview already active."
            if not await self._confirm_share("preview", identity):
                return "[CANCELLED] Desktop sharing was declined."
            if generation != self._share_generation or room is not self._room or not self._is_running:
                return "[CANCELLED] Desktop sharing request expired."
            await self._stop_desktop_video()
            if generation != self._share_generation:
                return "[CANCELLED] Desktop sharing request expired."
            self._desktop_preview = DesktopPreview(room, identity, monitor.latest_frame)
            self._desktop_preview.start()
            self._sharing_indicator(True)
            return "[DONE] Desktop preview started for the authorized participant."

    async def stop_desktop_preview(self):
        preview, self._desktop_preview = self._desktop_preview, None
        if preview is not None:
            await preview.stop()
            self._sharing_indicator(False)
        return "[DONE] Desktop preview stopped."

    async def _stop_desktop_video(self):
        video, self._desktop_video = self._desktop_video, None
        if video is not None:
            await video.stop()

    async def stop_desktop_sharing(self):
        self._share_generation += 1
        await asyncio.gather(self.stop_desktop_preview(), self._stop_desktop_video())
        return "[DONE] Desktop sharing stopped."

    async def _publish_text(self, text, topic):
        identity = getattr(self, "allowed_identity", None)
        if self._is_running and self._room and identity:
            await self._room.local_participant.publish_data(
                text.encode("utf-8"), reliable=True, topic=topic,
                destination_identities=[identity])

    async def send_text(self, text):
        await self._publish_text(text, "mark.response")

    async def speak_text(self, text):
        from livekit import rtc
        import numpy as np
        if not self._is_running or self._cancel.is_set():
            return
        self._is_speaking = True
        try:
            pcm = await self.tts_engine.synthesize(text)
            rate = self.tts_engine.sample_rate
            data = np.frombuffer(pcm, dtype="<i2")
            if rate != 24000 and data.size:
                size = round(data.size * 24000 / rate)
                data = np.interp(np.arange(size) * rate / 24000, np.arange(data.size), data).astype("<i2")
            for start in range(0, data.size, 480):
                if self._cancel.is_set() or not self._is_running:
                    break
                chunk = data[start:start+480]
                await self._source.capture_frame(rtc.AudioFrame(
                    data=chunk.tobytes(), sample_rate=24000, num_channels=1,
                    samples_per_channel=chunk.size))
            if not self._cancel.is_set():
                await self._source.wait_for_playout()
        finally:
            self._is_speaking = False

    async def interrupt(self):
        self._cancel.set()
        if self._source:
            self._source.clear_queue()

    async def stop(self):
        self._is_running = False
        self._pending_text = None
        await self.interrupt()
        await self.stop_desktop_sharing()
        for task in tuple(self._receivers):
            task.cancel()
        if self._receivers:
            await asyncio.gather(*tuple(self._receivers), return_exceptions=True)
        # Let in-flight tool workers observe cancellation before disconnecting.
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)
        room, source = self._room, self._source
        self._room = None
        self._source = None
        self._track_tasks.clear()
        try:
            if room:
                await room.disconnect()
        finally:
            if source and hasattr(source, "aclose"):
                await source.aclose()
