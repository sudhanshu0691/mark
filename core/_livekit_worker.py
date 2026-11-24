"""Opt-in LiveKit room transport using MARK's existing secure task runtime."""
from __future__ import annotations

import asyncio
import logging

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
        self._turn_task = None
        self._cancel = asyncio.Event()

    async def start(self, room, participant=None):
        from livekit import rtc
        if not self.markdown_adapter or not self.stt_engine or not self.tts_engine:
            raise RuntimeError("MARK voice adapters are not configured")
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
            self._is_running = False
            self._cancel.set()
            for task in tuple(self._receivers):
                task.cancel()

        @room.on("track_subscribed")
        def subscribed(track, publication, remote):
            if track.kind != rtc.TrackKind.KIND_AUDIO:
                return
            # Explicitly configured remote identity; never process arbitrary room users.
            allowed = getattr(self, "allowed_identity", None)
            if not allowed or remote.identity != allowed:
                return
            task = asyncio.create_task(self._receive_audio(track))
            self._receivers.add(task)
            task.add_done_callback(self._receivers.discard)

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
                if voiced and self._is_speaking:
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
                        if text and (self._turn_task is None or self._turn_task.done()):
                            self._turn_task = asyncio.create_task(self.handle_text(text))
                            self._tasks.add(self._turn_task)
                            self._turn_task.add_done_callback(self._turn_finished)
        finally:
            await stream.aclose()

    def _turn_finished(self, task):
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.error("Voice turn failed: %s", type(task.exception()).__name__)

    async def handle_text(self, text):
        from core import llm, brain
        from core.intent import is_conversation
        from memory.conversation import recent_messages
        from memory import db
        async with self._turn_lock:
            if not self._is_running:
                return
            self._cancel.clear()
            cfg = llm.load_config()
            active = await asyncio.to_thread(db.get_active_conversation)
            conversation_id = active["id"] if active else None
            history = await asyncio.to_thread(recent_messages, conversation_id=conversation_id)
            history = [{**m, "_local_only": not cfg.get("share_history_with_cloud", False)} for m in history]
            # Serialize against the existing typed-command worker.
            lock = self.markdown_adapter._local_command_lock
            if not lock.acquire(blocking=False):
                await self.speak_text("Another task is running. Please try again.")
                return
            try:
                if is_conversation(text):
                    reply = await asyncio.to_thread(llm.chat_raw, history + [{"role":"user","content":text}], role="fast")
                    answer = reply.get("content", "").strip()
                else:
                    answer = await asyncio.to_thread(
                        brain.run_goal, text, declarations=self.markdown_adapter._all_declarations(),
                        dispatch=self.markdown_adapter._dispatch_sync, history=history,
                        cancelled=self._cancel.is_set)
                if not answer:
                    answer = "No response was received."
                await asyncio.to_thread(db.append_conversation_turn, text, answer,
                                        channel="voice", conversation_id=conversation_id)
                if not self._cancel.is_set():
                    await self.send_text(answer)
                    await self.speak_text(answer.split("\nEXECUTION TRACE:")[0][:1800])
            finally:
                lock.release()

    async def send_text(self, text):
        if self._is_running and self._room:
            await self._room.local_participant.publish_data(text.encode("utf-8"), reliable=True,
                                                             topic="mark.response")

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
        await self.interrupt()
        for task in tuple(self._receivers):
            task.cancel()
        if self._receivers:
            await asyncio.gather(*tuple(self._receivers), return_exceptions=True)
        # Let in-flight tool workers observe cancellation before disconnecting.
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)
        if self._room:
            await self._room.disconnect()
        self._room = None
        self._source = None
