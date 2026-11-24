"""LLM Gateway for MARK - streaming token generation and context management.

This module provides a centralized LLM gateway that preserves the currently configured
local LLM and its inference runtime. It handles streaming, context management, tool calling,
and timeout/cancellation support.

Philosophy: One LLM gateway, used by all agents (conversation, system, browser, file, vision, etc.).
No duplicate intelligence engines for voice vs text.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, AsyncIterator, Optional, Tuple

logger = logging.getLogger(__name__)


class LLMResponseEvent:
    """Typed event from LLM streaming generation."""
    pass


class LLMStartEvent(LLMResponseEvent):
    """Emitted when LLM generation starts."""
    pass


class TextDeltaEvent(LLMResponseEvent):
    """Emitted when a new text token/delta is available."""
    text: str
    delta: str


class ToolCallEvent(LLMResponseEvent):
    """Emitted when the LLM requests a tool call."""
    tool_name: str
    tool_args: dict
    tool_call_id: str


class ToolResultEvent(LLMResponseEvent):
    """Emitted when a tool execution result is available."""
    tool_name: str
    tool_call_id: str
    result: str


class LLMCompleteEvent(LLMResponseEvent):
    """Emitted when LLM generation is complete."""
    full_text: str
    tool_calls: list | None = None


class LLMErrorEvent(LLMResponseEvent):
    """Emitted when LLM generation encounters an error."""
    error: str
    recoverable: bool = True


class CentralLLMGateway(ABC):
    """Abstract base for LLM gateways."""

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        timeout: float | None = None,
        *,
        cancellation_token: asyncio.Event | None = None,
    ) -> AsyncIterator[LLMResponseEvent]:
        raise NotImplementedError