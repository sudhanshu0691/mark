# core/inference/gemini.py
# Gemini inference provider for MARK

from __future__ import annotations

from typing import Dict, Any, Optional, List
import logging
import asyncio

from core.inference.base import InferenceProvider
from core.llm import load_config, _gemini_chat, _gemini_generate, LLMUnavailable

logger = logging.getLogger(__name__)


class GeminiProvider(InferenceProvider):
    """Gemini inference provider implementation."""

    def __init__(self, cfg: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(cfg)
        self.text_model = cfg.get("text_model", "") if cfg else ""
        self.live_model = cfg.get("live_model", "") if cfg else ""

    async def generate(self, messages: List[dict], **kwargs: Any) -> dict:
        """Generate a response from Gemini model."""
        cfg = load_config()
        text_model = cfg.get("gemini", {}).get("text_model", self.text_model or "")

        if not text_model:
            raise LLMUnavailable("Gemini text model is not configured.")

        temperature = kwargs.get("temperature", cfg.get("gemini", {}).get("temperature", 0.7))
        max_tokens = kwargs.get("max_tokens", cfg.get("gemini", {}).get("max_output_tokens", 8192))

        try:
            response = await asyncio.to_thread(
                _gemini_generate,
                "text",
                messages,
                cfg,
                native=False,
            )

            text = "".join(
                part.text
                for cand in response.candidates or []
                for part in (cand.content.parts or [])
                if part.text and not part.thought
            )

            tool_calls = []
            for fc in response.function_calls or []:
                tool_calls.append(
                    {
                        "function": {
                            "name": fc.name,
                            "arguments": dict(fc.args or {}),
                        }
                    }
                )

            return {
                "role": "assistant",
                "content": text,
                "tool_calls": tool_calls,
            }
        except LLMUnavailable:
            raise
        except Exception as e:
            raise LLMUnavailable(f"Gemini generation error: {e}")

    async def chat(self, messages: List[dict], **kwargs: Any) -> dict:
        """Chat-style generation with tool support."""
        return await self.generate(messages, **kwargs)

    def get_capabilities(self) -> dict[str, bool]:
        """Get provider capabilities."""
        return {
            "supports_tools": True,
            "supports_vision": True,
            "supports_streaming": True,
            "supports_json": True,
        }

    async def cancel_request(self) -> None:
        """Cancel an in-progress request."""
        pass