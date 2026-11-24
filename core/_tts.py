"""Speech synthesis adapter over MARK's existing Piper/Edge implementation."""
from __future__ import annotations
import asyncio
from typing import AsyncIterator

class PiperTTSEngine:
    def __init__(self, model_path=None, voice_name=None, language="en"):
        self.model_path = model_path
        self.voice_name = voice_name
        self.language = language
        self._tts = None
        self.sample_rate = 24000
        self._lock = asyncio.Lock()

    async def load(self):
        if self._tts is not None:
            return
        from actions.phone.speech import load_config, make_tts
        cfg = load_config()
        if self.model_path:
            cfg["piper_voice_" + self.language] = self.model_path
        if self.voice_name:
            cfg["edge_voice_" + self.language] = self.voice_name
        self._tts = make_tts(cfg)

    async def synthesize(self, text: str) -> bytes:
        if not text.strip():
            return b""
        async with self._lock:
            if self._tts is None:
                await self.load()
            audio, rate = await asyncio.to_thread(self._tts, text, self.language)
            self.sample_rate = rate
            if not audio:
                raise RuntimeError("Speech synthesis returned no audio")
            return audio

    async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]:
        # The existing speech backend returns an entire utterance, not live chunks.
        audio = await self.synthesize(text)
        if audio:
            yield audio
