"""Compatibility facade for cloud role selection."""
from __future__ import annotations
from typing import Any
from core.complexity_estimator import ComplexityEstimator
from core.llm import load_config

class AdaptiveIntelligenceRouter:
    def __init__(self):
        self.complexity_estimator = ComplexityEstimator()

    def route(self, user_input: str, context: str = "") -> dict[str, Any]:
        cfg = load_config()
        complexity = self.complexity_estimator.estimate(user_input, context)
        provider = "gemini" if cfg.get("backend") == "gemini" else cfg.get("primary_provider", "groq")
        models = cfg.get("providers", {}).get(provider, {})
        role = "tool" if complexity["requires_tools"] else "text"
        return {"provider": provider, "model": models.get(role + "_model", models.get("text_model")),
                "vision_model": cfg.get("gemini", {}).get("vision_model"),
                "complexity_level": complexity["level"], "reasoning_depth": complexity["reasoning_depth"],
                "estimated_tokens": complexity["estimated_tokens"], "requires_tools": complexity["requires_tools"],
                "capabilities": {"supports_tools": True, "supports_vision": provider == "gemini"},
                "hardware_info": {}}
