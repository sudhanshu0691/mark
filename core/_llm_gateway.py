"""Async adapter over the central cloud LLM; emits complete responses."""
from __future__ import annotations
import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator

class LLMResponseEvent:
    """Base response event."""

@dataclass
class LLMStartEvent(LLMResponseEvent):
    pass

@dataclass
class TextDeltaEvent(LLMResponseEvent):
    text: str
    delta: str

@dataclass
class ToolCallEvent(LLMResponseEvent):
    tool_name: str
    tool_args: dict
    tool_call_id: str

@dataclass
class ToolResultEvent(LLMResponseEvent):
    tool_name: str
    tool_call_id: str
    result: str

@dataclass
class LLMCompleteEvent(LLMResponseEvent):
    full_text: str
    tool_calls: list | None = None

@dataclass
class LLMErrorEvent(LLMResponseEvent):
    error: str
    recoverable: bool = True

class CentralLLMGateway(ABC):
    @abstractmethod
    async def generate(self, prompt: str, system: str | None = None,
                       temperature: float = 0.0, max_tokens: int | None = None,
                       timeout: float | None = None, *,
                       cancellation_token: asyncio.Event | None = None) -> AsyncIterator[LLMResponseEvent]:
        raise NotImplementedError

class CloudLLMGateway(CentralLLMGateway):
    def __init__(self, config_path=None):
        self.config_path = config_path

    async def generate(self, prompt, system=None, temperature=0.0,
                       max_tokens=None, timeout=None, *, cancellation_token=None):
        from core import llm
        if cancellation_token is not None and cancellation_token.is_set():
            return
        yield LLMStartEvent()
        try:
            result = await asyncio.wait_for(asyncio.to_thread(
                llm.generate, prompt, role="text", system=system,
                temperature=temperature, max_tokens=max_tokens), timeout=timeout)
        except Exception as exc:
            yield LLMErrorEvent(type(exc).__name__)
            return
        if cancellation_token is not None and cancellation_token.is_set():
            return
        # Buffered completion is explicit; no claim of token streaming.
        yield LLMCompleteEvent(result)

# Compatibility with the old import. All inference is cloud routed.
LocalLLMGateway = CloudLLMGateway
