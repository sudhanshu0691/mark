"""Compatibility facade sharing the master command routing policy."""
from __future__ import annotations
from core.llm import load_config, model_for
from core.agents.orchestrator import select_route

class AdaptiveIntelligenceRouter:
    def route(self, user_input: str, context: str = "") -> dict:
        route = select_route(user_input)
        cfg = load_config()
        role = route.role
        provider = None if role is None else ("gemini" if cfg.get("backend") == "gemini" else cfg.get("primary_provider", "groq"))
        return {"provider": provider, "model": model_for(role, cfg) if role else None,
                "role": role, "vision_model": cfg.get("gemini", {}).get("vision_model"),
                "complexity_level": route.complexity, "reasoning_depth": route.steps,
                "estimated_tokens": None, "requires_tools": route.kind != "conversation",
                "capabilities": {"supports_tools": role is not None, "supports_vision": provider == "gemini"},
                "hardware_info": {}}
