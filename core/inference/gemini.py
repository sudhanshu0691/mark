"""Async compatibility adapter using the shared cloud inference gateway."""
from __future__ import annotations
import asyncio
from core.inference.base import InferenceProvider
from core import llm

class GeminiProvider(InferenceProvider):
    async def generate(self, messages, **kwargs):
        return await asyncio.to_thread(
            llm.chat_raw, messages, role=kwargs.get("role", "text"),
            tools=kwargs.get("tools"), temperature=kwargs.get("temperature"),
            max_tokens=kwargs.get("max_tokens"), json_mode=kwargs.get("json_mode", False),
            cancelled=kwargs.get("cancelled"), provider_override="gemini")

    async def chat(self, messages, **kwargs):
        return await self.generate(messages, **kwargs)

    def get_capabilities(self):
        return {"supports_tools": True, "supports_vision": True,
                "supports_streaming": False, "supports_json": True}
