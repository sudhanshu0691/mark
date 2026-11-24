"""Provider registry for MARK's inference system."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from core.llm import InferenceProvider, LLMUnavailable


class ProviderRegistry:
    """Registry for managing inference providers."""

    def __init__(self):
        self.providers: dict[str, InferenceProvider] = {}
        self._default: str | None = None

    def register(self, name: str, provider: InferenceProvider) -> None:
        """Register a provider."""
        self.providers[name] = provider
        if self._default is None:
            self._default = name

    def unregister(self, name: str) -> None:
        """Unregister a provider."""
        if name in self.providers:
            del self.providers[name]
            if self._default == name:
                self._default = list(self.providers.keys())[0] if self.providers else None

    def get(self, name: str | None = None) -> InferenceProvider:
        """Get a provider by name, or the default."""
        name = name or self._default
        if name not in self.providers:
            raise LLMUnavailable(f"Provider '{name}' not registered.")
        return self.providers[name]

    def available_providers(self, capabilities: dict[str, bool] | None = None) -> list[str]:
        """Get list of providers matching capabilities."""
        result = []
        for name, provider in self.providers.items():
            caps = provider.get_capabilities()
            if capabilities is None or all(caps.get(k, False) == v for k, v in capabilities.items()):
                result.append(name)
        return result

    def health_all(self) -> dict[str, dict]:
        """Check health of all providers."""
        result = {}
        for name, provider in self.providers.items():
            result[name] = provider.health_check()
        return result

    def select_provider(self, capabilities: dict[str, bool],
                        preferences: dict[str, Any] | None = None) -> str:
        """Select the best provider for given capabilities."""
        candidates = self.available_providers(capabilities)
        if not candidates:
            raise LLMUnavailable("No provider matches the required capabilities.")

        preferences = preferences or {}
        scored = []
        for name in candidates:
            provider = self.providers[name]
            score = 0

            if preferences.get("prefer_local", True) and name == "ollama":
                score += 100

            if preferences.get("prefer_fast", False):
                score += provider.stats.get("latency_ms", 0) * -0.01

            if preferences.get("prefer_healthy", True):
                if provider.health.get("ok", False):
                    score += 50

            scored.append((name, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[0][0]