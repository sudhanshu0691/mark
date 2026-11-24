# core/inference/base.py
# Base inference provider contract for MARK

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
import asyncio
import json
import logging

logger = logging.getLogger(__name__)


class InferenceProvider(ABC):
    """Base class for all inference providers."""

    def __init__(self, cfg: Optional[Dict[str, Any]] = None) -> None:
        self.cfg = cfg or {}
        self.health: dict[str, Any] = {"ok": False, "message": "not initialized"}
        self.stats: dict[str, Any] = {"requests": 0, "tokens": 0, "latency_ms": 0}

    @abstractmethod
    async def generate(self, messages: List[dict], **kwargs: Any) -> dict:
        """Generate a response from the model."""
        pass

    @abstractmethod
    async def chat(self, messages: List[dict], **kwargs: Any) -> dict:
        """Chat-style generation with tool support."""
        pass

    def is_available(self) -> bool:
        """Check if the provider is available."""
        return self.health.get("ok", False)

    def health_check(self) -> dict[str, Any]:
        """Check provider health."""
        return self.health

    @abstractmethod
    def get_capabilities(self) -> dict[str, bool]:
        """Get provider capabilities."""
        pass

    async def cancel_request(self) -> None:
        """Cancel an in-progress request."""
        pass


class ProviderRegistry:
    """Registry for inference providers."""

    def __init__(self) -> None:
        self._providers: dict[str, InferenceProvider] = {}
        self._provider_names: dict[str, str] = {}

    def register(self, name: str, provider: InferenceProvider) -> None:
        """Register an inference provider."""
        self._providers[name] = provider
        self._provider_names[provider.__class__.__name__] = name

    def get(self, name: str) -> InferenceProvider | None:
        """Get a provider by name."""
        return self._providers.get(name)

    def list(self) -> list[str]:
        """List registered provider names."""
        return list(self._providers.keys())

    def get_available(self) -> list[str]:
        """List available (healthy) providers."""
        return [
            name for name, provider in self._providers.items()
            if provider.is_available()
        ]


# Global registry instance
_provider_registry: Optional[ProviderRegistry] = None


def get_provider_registry() -> ProviderRegistry:
    """Get the global provider registry."""
    global _provider_registry
    if _provider_registry is None:
        _provider_registry = ProviderRegistry()
    return _provider_registry


def set_provider_registry(registry: ProviderRegistry) -> None:
    """Set the global provider registry."""
    global _provider_registry
    _provider_registry = registry