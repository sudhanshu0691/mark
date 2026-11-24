# core/inference/__init__.py
# Inference providers for MARK

from __future__ import annotations

from .base import InferenceProvider, ProviderRegistry, get_provider_registry, set_provider_registry
from .gemini import GeminiProvider
from .manager import InferenceManager