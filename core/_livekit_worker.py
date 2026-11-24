"""Opt-in LiveKit room transport using MARK's existing secure task runtime."""
from __future__ import annotations

import asyncio
import logging
from core.voice_stream import SentenceBuffer

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

    async def start(self, room, participant=None):
        from livekit import rtc
        if not self.markdown_adapter or not self.stt_engine or not self.tts_engine:
            raise RuntimeError("MARK voice adapters are not configured")
        if self._room is not None:
            await self.stop()
        await self.stt_engine.load()
        await self.tts_engine.load()
        self._room = room
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
            for task in tuple(self._receivers):
                task.cancel()

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
        import numpy as np
        stream = rtc.AudioStream(track, sample_rate=16000, num_channels=1)
        frames = bytearray()
        silence = 0
        try:
            async for event in stream:
                if not self._is_running:
                    break
                data = bytes(event.frame.data)
                samples = np.frombuffer(data, dtype="<i2").astype(np.float32)
                voiced = bool(samples.size and np.sqrt(np.mean(samples * samples)) > 450)
                if voiced and self._turn_task is not None and not self._turn_task.done():
                    await self.interrupt()
                if voiced or frames:
                    frames.extend(data)
                silence = 0 if voiced else silence + len(data)
                if frames and (silence >= 19200 or len(frames) >= 320000):
                    utterance = bytes(frames)
                    frames.clear()
                    silence = 0
                    if len(utterance) >= 9600:
                        text = await self.stt_engine.transcribe(utterance, 16000)
                        if text:
                            self.submit_text(text)
        finally:
            await stream.aclose()

    def submit_text(self, text):
        if not self._is_running:
            return
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
                    await asyncio.to_thread(db.append_conversation_turn, text, head,
                                            channel="voice", conversation_id=conversation_id)
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
