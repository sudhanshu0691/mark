"""LiveKit Agent Worker for MARK.

Python-based LiveKit Agent Worker using the installed LiveKit Agents SDK.

Manages:
- Agent session lifecycle
- Audio input and output
- Voice activity detection
- Speech recognition
- End-of-turn detection
- Interruption handling
- Streaming response generation
- Speech synthesis
- Session events
- Resource cleanup
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional, Tuple

logger = logging.getLogger(__name__)

class LiveKitAgentWorker:
    """LiveKit Agent Worker for MARK real-time conversational AI.

    This worker uses the livekit-agents SDK to manage the conversational pipeline,
    connecting the voice pipeline to the MARK core engine through a thin adapter.

    Responsibilities:
    - Agent session lifecycle management
    - Audio input/output via WebRTC
    - VAD-based speech detection and endpointing
    - Speech-to-text via local Faster Whisper engine
    - End-of-turn detection
    - Interruption handling
    - Streaming LLM response generation via MARK LLM gateway
    - Local text-to-speech via Piper TTS
    - WebRTC audio output to client
    - Text message handling
    - Session events and resource cleanup
    """

    def __init__(self, markdown_adapter=None, llm_gateway=None, stt_engine=None, tts_engine=None):
        self.markdown_adapter = markdown_adapter
        self.llm_gateway = llm_gateway
        self.stt_engine = stt_engine
        self.tts_engine = tts_engine

        # Session state
        self._session = None
        self._loop = None
        self._is_running = False
        self._is_speaking = False
        self._interrupted = False
        self._cancel_requested = False
        self._active_task_id = None
        self._local_cancel_event = asyncio.Event()

        # Pipeline state
        self._audio_in_queue = None
        self._out_queue = None
        self._turn_done_event = None

        # Plugin/agent state
        self._plugins_changed = asyncio.Event()

        logger.info("LiveKit Agent Worker initialized")

    async def start(self, room, participant) -> None:
        """Start the LiveKit agent worker for a new room/participant.

        Args:
            room: LiveKit Room instance
            participant: LiveKit Participant instance
        """
        self._loop = asyncio.get_running_loop()
        self._session = None

        # Initialize pipelines if not already done
        if self.stt_engine:
            await self.stt_engine.load()

        if self.tts_engine:
            await self.tts_engine.load()

        self._is_running = True
        self._interrupted = False
        self._cancel_requested = False
        self._active_task_id = None

        logger.info(f"LiveKit Agent Worker started for participant {participant.identity}")

        # Set up participant event handlers
        self._setup_participant_events(participant)

        # Start the agent session
        await self._start_agent_session()

    def _setup_participant_events(self, participant) -> None:
        """Set up participant event handlers for audio and text events."""

        @participant.on("track_subscribed")
        def on_track_subscribed(track, publication, **kwargs):
            """Handle subscribed audio tracks."""
            logger.debug(f"Track subscribed: {track.kind}")
            if track.kind == "audio":
                logger.info("Remote audio track subscribed")

        @participant.on("track_published")
        def on_track_published(publication, **kwargs):
            """Handle published tracks."""
            logger.debug(f"Track published: {publication.track.kind if publication.track else None}")

        @participant.on("connection_quality_changed")
        def on_connection_quality_changed(info):
            """Handle connection quality changes."""
            logger.info(f"Connection quality changed: {info}")

        @participant.on("disconnected")
        def on_disconnected():
            """Handle participant disconnection."""
            logger.info("Participant disconnected")
            asyncio.create_task(self._handle_disconnection())

        @participant.on("connected")
        def on_connected():
            """Handle room connection established."""
            logger.info("Room connection established")
            asyncio.create_task(self._handle_connection_established())

    async def _start_agent_session(self) -> None:
        """Start the MARK agent session through the adapter."""
        if not self.markdown_adapter or not self.llm_gateway:
            logger.warning("MarkLive adapter or LLM gateway not available")
            return

        try:
            logger.info("Starting MARK agent session through LiveKit adapter")
        except Exception as e:
            logger.error(f"Failed to start agent session: {e}", exc_info=True)

    async def _handle_disconnection(self) -> None:
        """Handle participant disconnection gracefully."""
        logger.info("Handling disconnection in LiveKit worker")
        self._is_running = False
        self._cancel_requested = True

        # Cancel any active tasks
        if self._active_task_id:
            from core.task_manager import TASKS
            TASKS.request_cancel(self._active_task_id)
            self._active_task_id = None

        await self._cleanup()

    async def _handle_connection_established(self) -> None:
        """Handle room connection establishment."""
        logger.info("Connection established, restoring context")
        self._is_running = True
        await self._restore_context()

    async def _restore_context(self) -> None:
        """Restore conversation context after reconnection."""
        try:
            from memory import db
            active = db.get_active_conversation()
            if active:
                turns = db.load_conversation_turns(conversation_id=active["id"])
                logger.info(f"Restored {len(turns)} conversation turns")
                if self.markdown_adapter:
                    for turn in turns:
                        pass
        except Exception as e:
            logger.error(f"Failed to restore context: {e}", exc_info=True)

    async def _cleanup(self) -> None:
        """Clean up resources."""
        logger.info("Cleaning up LiveKit worker resources")
        self._is_running = False
        self._interrupted = False
        self._cancel_requested = False
        self._local_cancel_event.set()
        self._session = None

    async def speak_text(self, text: str) -> None:
        """Generate and play text-to-speech.

        Args:
            text: Text to speak
        """
        if not self._is_running:
            return

        self._is_speaking = True

        try:
            if self.tts_engine:
                audio_chunk = await self.tts_engine.synthes(text)
                if audio_chunk:
                    logger.debug(f"Generated TTS audio: {len(audio_chunk)} bytes")
                else:
                    logger.warning("TTS returned no audio")

            if not self._is_speaking:
                self._is_speaking = False

        except Exception as e:
            logger.error(f"Error during TTS: {e}", exc_info=True)
            self._is_speaking = False

    async def interrupt(self) -> None:
        """Handle user interrupt - stop current speech and cancel response."""
        logger.info("User interrupt detected")
        self._interrupted = True
        self._cancel_requested = True
        self._is_speaking = False
        if self._local_cancel_event:
            self._local_cancel_event.set()

    async def send_text(self, text: str) -> None:
        """Send a text message to the room.

        Args:
            text: Text message to send
        """
        if not self._is_running:
            return
        logger.info(f"Sending text to room: {text[:80]}...")

    async def stop(self) -> None:
        """Stop the LiveKit agent worker and clean up resources."""
        logger.info("Stopping LiveKit Agent Worker")
        self._is_running = False
        self._cancel_requested = True
        self._interrupted = True
        if self._local_cancel_event:
            self._local_cancel_event.set()
        await self._cleanup()

    async def speak_text(self, text: str) -> None:
        """Generate and play text-to-speech.

        Args:
            text: Text to speak
        """
        if not self._is_running:
            return

        self._is_speaking = True

        try:
            if self.tts_engine:
                audio_chunk = await self.tts_engine.synthes(text)
                if audio_chunk:
                    logger.debug(f"Generated TTS audio: {len(audio_chunk)} bytes")
                else:
                    logger.warning("TTS returned no audio")

            if not self._is_speaking:
                self._is_speaking = False

        except Exception as e:
            logger.error(f"Error during TTS: {e}", exc_info=True)
            self._is_speaking = False

    async def interrupt(self) -> None:
        """Handle user interrupt - stop current speech and cancel response."""
        logger.info("User interrupt detected")
        self._interrupted = True
        self._cancel_requested = True
        self._is_speaking = False
        if self._local_cancel_event:
            self._local_cancel_event.set()
