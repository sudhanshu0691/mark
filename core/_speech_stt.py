"""Utterance-level speech recognition; no local language model."""
from __future__ import annotations

import asyncio

class STTEngine:
    def __init__(self, model_size="base", device="cpu", compute_type="int8", language=None):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.language = language
        self._model = None
        self._lock = asyncio.Lock()

    async def load(self):
        async with self._lock:
            if self._model is None:
                from faster_whisper import WhisperModel
                self._model = await asyncio.to_thread(
                    WhisperModel, self.model_size, device=self.device,
                    compute_type=self.compute_type, local_files_only=True)

    async def transcribe(self, pcm: bytes, sample_rate: int = 16000) -> str:
        if not pcm:
            return ""
        await self.load()
        from actions.phone.audio import _to_mono16k
        def recognize():
            audio = _to_mono16k(pcm, 1, sample_rate) / 32768.0
            segments, _ = self._model.transcribe(
                audio, language=self.language, vad_filter=True,
                beam_size=1, condition_on_previous_text=False)
            return " ".join(segment.text.strip() for segment in segments).strip()
        async with self._lock:
            return await asyncio.to_thread(recognize)
