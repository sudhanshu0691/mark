# core/intelligence/router.py
# Model router for MARK - routes requests to appropriate models based on intent, complexity, and resources

from __future__ import annotations

from typing import Dict, Any, Optional, Tuple
from .intent import Intent, Complexity, classify_intent, assess_complexity, route_for_intent


class ModelRouter:
    """Router that automatically determines the best model for each request."""
    
    def __init__(self, available_models: Dict[str, str]) -> None:
        """
        Initialize the model router.
        
        Args:
            available_models: Dict mapping model roles to model names
                            e.g., {"fast": "qwen3:4b", "text": "qwen3:4b", "code": "qwen3:4b", "vision": "qwen2.5vl:3b"}
        """
        self.available_models = available_models or {}
        self._history: list[dict] = []
        self._max_history = 50
    
    def route(self, user_query: str) -> dict:
        """
        Route a user query to the appropriate model.
        
        This is the main entry point for the adaptive intelligence engine.
        It automatically:
        1. Classifies the user's intent
        2. Assesses the complexity
        3. Routes to the appropriate model considering available resources
        
        Returns:
            dict with keys: intent, complexity, model, reasoning_depth, extraction
        """
        # Step 1: Classify intent
        intent, complexity, extraction = classify_intent(user_query)
        
        # Step 2: Assess complexity
        complexity = assess_complexity(user_query, intent, extraction)
        
        # Step 3: Route to appropriate model
        model, routing = route_for_intent(intent, complexity, self.available_models)
        
        # Step 4: Record routing decision for telemetry
        routing_decision = {
            "intent": intent.value,
            "complexity": complexity.value,
            "model": model,
            "reasoning_depth": routing.get("reasoning_depth", "standard"),
            "extraction": extraction,
        }
        self._record_routing(routing_decision)
        
        # Step 5: Return comprehensive routing info
        result: dict = {
            "intent": intent,
            "complexity": complexity,
            "model": model,
            "reasoning_depth": routing.get("reasoning_depth", "standard"),
            "extraction": extraction,
            "auto_routed": True,
        }
        
        return result
    
    def _record_routing(self, decision: dict) -> None:
        """Record routing decision for analysis."""
        self._history.append(decision)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]
    
    def get_routing_history(self) -> list[dict]:
        """Get routing decision history."""
        return list(self._history)
    
    def get_stats(self) -> dict:
        """Get routing statistics."""
        if not self._history:
            return {"total_routes": 0}
        
        intent_counts: dict[str, int] = {}
        complexity_counts: dict[str, int] = {}
        model_usage: dict[str, int] = {}
        
        for decision in self._history:
            intent_counts[decision.get("intent", "unknown")] = \
                intent_counts.get(decision.get("intent", "unknown"), 0) + 1
            complexity_counts[decision.get("complexity", "unknown")] = \
                complexity_counts.get(decision.get("complexity", "unknown"), 0) + 1
            model = decision.get("model", "")
            if model:
                model_usage[model] = model_usage.get(model, 0) + 1
        
        return {
            "total_routes": len(self._history),
            "intent_distribution": intent_counts,
            "complexity_distribution": complexity_counts,
            "model_usage": model_usage,
        }


# Global router instance
_model_router: Optional[ModelRouter] = None

def get_model_router(available_models: Dict[str, str] | None = None) -> ModelRouter:
    """Get the global model router instance."""
    global _model_router
    if _model_router is None:
        if available_models is None:
            available_models = {"fast": "qwen3:4b", "text": "qwen3:4b", "code": "qwen3:4b", "vision": "qwen2.5vl:3b"}
        _model_router = ModelRouter(available_models)
    return _model_router

def set_model_router(router: ModelRouter) -> None:
    """Set the global model router instance."""
    global _model_router
    _model_router = router