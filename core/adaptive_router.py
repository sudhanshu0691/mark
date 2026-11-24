"""Adaptive intelligence router for MARK."""

from __future__ import annotations

from core.complexity_estimator import ComplexityEstimator
from core.hardware_manager import HardwareAwareModelManager
from core.provider_registry import ProviderRegistry

from core.llm import LLMUnavailable


class AdaptiveIntelligenceRouter:
    """Router that automatically determines the best intelligence configuration."""

    def __init__(self):
        self.complexity_estimator = ComplexityEstimator()
        self.hardware_manager = HardwareAwareModelManager()
        self.registry = ProviderRegistry()

        # Initialize default providers from config
        from core.llm import load_config
        cfg = load_config()
        backend = cfg.get("backend", "ollama").lower()

        if backend in ("ollama", "hybrid"):
            from core.llm import OllamaProvider
            ollama_provider = OllamaProvider(cfg)
            self.registry.register("ollama", ollama_provider)

        if backend in ("gemini", "hybrid"):
            from core.llm import GeminiProvider
            gemini_provider = GeminiProvider(cfg)
            self.registry.register("gemini", gemini_provider)

    def route(self, user_input: str, context: str = "") -> dict[str, Any]:
        """Route user input to the optimal model and configuration."""
        # Estimate complexity
        complexity = self.complexity_estimator.estimate(user_input, context)

        # Get hardware-aware model recommendations
        text_model = self.hardware_manager.recommend_model("text")
        vision_model = self.hardware_manager.recommend_model("vision")

        # Determine required capabilities
        requires_tools = complexity["requires_tools"]
        capabilities = {"supports_tools": requires_tools}

        # Select optimal provider
        try:
            provider_name = self.registry.select_provider(capabilities)
        except LLMUnavailable:
            provider_name = "ollama"  # fallback

        provider = self.registry.get(provider_name)

        # Get model for role
        text_model = provider.get_model_for_role("text") or text_model
        vision_model = provider.get_model_for_role("vision") or vision_model

        return {
            "provider": provider_name,
            "model": text_model,
            "vision_model": vision_model,
            "complexity_level": complexity["level"],
            "reasoning_depth": complexity["reasoning_depth"],
            "estimated_tokens": complexity["estimated_tokens"],
            "requires_tools": requires_tools,
            "capabilities": provider.get_capabilities(),
            "hardware_info": {
                "cpu_count": self.hardware_manager.cpu_count,
                "total_ram_gb": self.hardware_manager.total_ram_gb,
                "gpu_available": self.hardware_manager.gpu_available,
                "gpu_memory_gb": self.hardware_manager.gpu_memory_gb,
            },
        }