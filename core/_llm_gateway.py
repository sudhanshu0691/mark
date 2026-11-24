"""Async adapter over the central cloud LLM with incremental text events."""
from __future__ import annotations
import asyncio
import threading
from contextlib import suppress
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
        loop = asyncio.get_running_loop()
        queue = asyncio.Queue()
        stop = threading.Event()
        messages = ([{"role": "system", "content": system}] if system else [])
        messages.append({"role": "user", "content": prompt})

        def publish(kind, value):
            if not stop.is_set():
                loop.call_soon_threadsafe(queue.put_nowait, (kind, value))

        def run():
            try:
                result = llm.chat_raw(messages, role="text", temperature=temperature,
                                      max_tokens=max_tokens, on_delta=lambda s: publish("delta", s),
                                      cancelled=stop.is_set)
                publish("complete", result.get("content", ""))
            except Exception as exc:
                publish("error", type(exc).__name__)

        task = asyncio.create_task(asyncio.to_thread(run))
        deadline = loop.time() + timeout if timeout is not None else None
        text = ""
        try:
            while True:
                if cancellation_token is not None and cancellation_token.is_set():
                    return
                if deadline is not None and loop.time() >= deadline:
                    yield LLMErrorEvent("TimeoutError")
                    return
                try:
                    kind, value = await asyncio.wait_for(queue.get(), timeout=0.1)
                except asyncio.TimeoutError:
                    continue
                if kind == "delta":
                    text += value
                    yield TextDeltaEvent(text=text, delta=value)
                elif kind == "complete":
                    yield LLMCompleteEvent(value)
                    return
                else:
                    yield LLMErrorEvent(value)
                    return
        finally:
            stop.set()
            # Cancelling the await does not terminate a blocking SDK thread.
            # The SDK's request timeout and the stop flag bound subsequent work.
            if not task.done():
                task.cancel()
            with suppress(asyncio.CancelledError):
                await task


# Compatibility with the old import. All inference is cloud routed.
LocalLLMGateway = CloudLLMGateway
