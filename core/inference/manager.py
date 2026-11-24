# core/inference/manager.py
# Unified inference manager for MARK - handles model routing and provider selection

from __future__ import annotations

from typing import Dict, Any, Optional, List
import logging

from core.inference.base import InferenceProvider, get_provider_registry
from core.intelligence.intent import (
    classify_intent,
    assess_complexity,
    Intent,
    Complexity,
)
from core.intelligence.complexity import ComplexityAnalyzer
from core.llm import load_config


class InferenceManager:
    """Manages inference routing and provider selection for MARK."""

    def __init__(self, available_models: Optional[Dict[str, str]] = None) -> None:
        """
        Initialize the inference manager.
        
        Args:
            available_models: Dict mapping model roles to model names
        """
        self.model_router = available_models or {}
        self.complexity_analyzer = ComplexityAnalyzer()
        self.provider_registry = get_provider_registry()
        self._routing_history: list = []
        self._max_history = 50

    async def process_query(
        self,
        user_query: str,
        conversation_history: Optional[List[dict]] = None,
        force_model: Optional[str] = None,
        force_intent: Optional[Intent] = None,
        tools: Optional[List[dict]] = None,
    ) -> dict:
        """
        Process a user query through the adaptive intelligence engine.
        """
        # Step 1: Classify intent and assess complexity
        intent, complexity, extraction = classify_intent(user_query)

        if force_intent:
            intent = force_intent

        complexity = assess_complexity(user_query, intent, extraction)

        # Step 2: Route to appropriate model
        model = force_model or self._select_model(intent, complexity)

        # Step 3: Record routing decision
        routing_decision = {
            "user_query": user_query,
            "intent": intent.value,
            "complexity": complexity.value,
            "model": model,
            "reasoning_depth": "standard",
            "extraction": extraction,
        }
        self._record_routing(routing_decision)

        # Step 4: Get appropriate provider
        provider = self._get_provider(model or "ollama")

        # Step 5: Prepare messages
        messages = self._prepare_messages(
            user_query,
            conversation_history or [],
            intent,
            extraction,
        )

        # Step 6: Execute inference

        try:
            response = await provider.chat(
                messages,
                tools=tools,
                temperature=None,
                max_tokens=None,
            )

            result = {
                "status": "completed",
                "response": response.get("content", ""),
                "model": model or "ollama",
                "intent": intent,
                "complexity": complexity,
                "tool_calls": response.get("tool_calls", []),
                "auto_routed": True,
                "routing_decision": routing_decision,
            }

            self._record_result(result)
            return result
        except Exception as e:
            logging.error("Inference error: %s: %s", type(e).__name__, e)
            return {
                "status": "failed",
                "response": f"I encountered an error: {str(e)}",
                "model": model or "ollama",
                "intent": intent,
                "complexity": complexity,
                "auto_routed": False,
                "error": str(e),
            }

    def _select_model(self, intent: Intent, complexity: Complexity) -> str:
        """Select model based on intent and complexity."""
        model = self.model_router.get("model") if isinstance(self.model_router, dict) else None
        if model:
            return model
        
        # Default model selection
        if intent == Intent.CONVERSATION and complexity == Complexity.LOW:
            return "fast"
        elif intent == Intent.CODE:
            return "code"
        elif intent == Intent.VISION:
            return "vision"
        elif complexity == Complexity.DEEP:
            return "large"
        elif complexity == Complexity.HIGH:
            return "medium"
        else:
            return "text"

    def _get_provider(self, model_name: str) -> InferenceProvider:
        """Get the appropriate inference provider for a model."""
        provider = self.provider_registry.get(model_name)
        if provider:
            return provider

        from core.inference.ollama import OllamaProvider
        cfg = load_config()
        provider = OllamaProvider(cfg)
        self.provider_registry.register("ollama", provider)
        return provider

    def _prepare_messages(
        self,
        user_query: str,
        history: List[dict],
        intent: Intent,
        extraction: Dict[str, Any],
    ) -> List[dict]:
        """Prepare messages for inference."""
        messages: List[dict] = []

        system_prompt = self._get_system_prompt(intent, extraction)
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        for msg in history[-10:]:
            if isinstance(msg, dict) and "role" in msg and "content" in msg:
                messages.append(msg)

        messages.append({"role": "user", "content": user_query})

        return messages

    def _get_system_prompt(self, intent: Intent, extraction: Dict[str, Any]) -> Optional[str]:
        """Get system prompt based on intent."""
        prompts = {
            Intent.CONVERSATION: "You are MARK, a helpful AI assistant.",
            Intent.CODE: "You are MARK, a coding assistant.",
        }
        return prompts.get(intent)

    def _record_routing(self, decision: dict) -> None:
        """Record routing decision for telemetry."""
        self._routing_history.append(decision)
        if len(self._routing_history) > self._max_history:
            self._routing_history = self._routing_history[-self._max_history:]

    def _record_result(self, result: dict) -> None:
        """Record inference result for telemetry."""
        pass

    def get_routing_history(self) -> list:
        """Get routing decision history."""
        return list(self._routing_history)

    def get_stats(self) -> dict:
        """Get inference statistics."""
        return {
            "total_routes": len(self._routing_history),
        }