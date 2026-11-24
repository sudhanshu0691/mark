# core/intelligence/complexity.py
# Complexity analyzer for MARK - analyzes task complexity and allocates reasoning resources

from __future__ import annotations

import re
from typing import Dict, Any, Optional, Tuple
from .intent import Intent, Complexity, classify_intent, assess_complexity


class ComplexityAnalyzer:
    """Analyzes task complexity and determines required reasoning depth."""

    MODERATE_KEYWORD_THRESHOLD = 2

    def __init__(self) -> None:
        self._analysis_history: list[dict] = []
        self._max_history = 100

    def analyze(self, user_query: str, context: Dict[str, Any] | None = None) -> dict:
        """Analyze the complexity of a user query."""
        if not user_query or not user_query.strip():
            return {
                "intent": Intent.CONVERSATION,
                "complexity": Complexity.LOW,
                "reasoning_allocations": [],
                "estimated_tokens": 0,
                "model_preference": "efficient",
            }

        q = user_query.strip()
        q_lower = q.lower()

        # Step 1: Classify intent
        intent, base_complexity, extraction = classify_intent(user_query)

        # Step 2: Assess base complexity
        complexity = assess_complexity(user_query, intent, extraction)

        # Step 3: Adjust complexity based on query characteristics
        adjusted_complexity = self._adjust_complexity(complexity, q, q_lower, extraction)

        # Step 4: Calculate reasoning allocations
        reasoning_allocations = self._calculate_reasoning_allocations(adjusted_complexity, extraction)

        # Step 5: Estimate token requirements
        estimated_tokens = self._estimate_tokens(q, adjusted_complexity, extraction)

        # Step 6: Determine model preference
        model_preference = self._determine_model_preference(adjusted_complexity, extraction)

        result: dict = {
            "intent": intent,
            "complexity": adjusted_complexity,
            "reasoning_allocations": reasoning_allocations,
            "estimated_tokens": estimated_tokens,
            "model_preference": model_preference,
            "extraction": extraction,
        }

        self._record_analysis(result)
        return result

    def _adjust_complexity(
        self,
        base: Complexity,
        q: str,
        q_lower: str,
        extraction: Dict[str, Any],
    ) -> Complexity:
        """Adjust complexity based on query characteristics."""
        adjustments = []

        # Length-based adjustment
        if len(q) > 300:
            if base == Complexity.LOW:
                adjusted = Complexity.MEDIUM
            elif base == Complexity.MEDIUM:
                adjusted = Complexity.HIGH
            else:
                adjusted = Complexity.DEEP
        elif len(q) > 150:
            if base == Complexity.LOW:
                adjusted = Complexity.MEDIUM
            elif base == Complexity.MEDIUM:
                adjusted = Complexity.HIGH
            else:
                adjusted = base
        else:
            adjusted = base

        # Keyword-based adjustments
        complex_indicators = [
            "analyze", "examine", "investigate", "research",
            "compare", "contrast", "evaluate", "critique",
            "prove", "derive", "optimize", "troubleshoot",
        ]
        simple_indicators = [
            "hello", "hi", "hey", "how are you", "what's up",
            "tell me", "show me", "just", "quick",
        ]

        complex_count = sum(1 for ind in complex_indicators if ind in q_lower)
        simple_count = sum(1 for ind in simple_indicators if ind in q_lower)

        if complex_count >= self.MODERATE_KEYWORD_THRESHOLD:
            if adjusted.value == "low":
                adjusted = Complexity.MEDIUM
            elif adjusted.value == "medium":
                adjusted = Complexity.HIGH
            adjustments.append(f"complex_keywords:{complex_count}")

        if simple_count >= 2 and len(q) < 50:
            if adjusted.value not in ("low",):
                adjusted = Complexity.LOW
            adjustments.append(f"simple_keywords:{simple_count}")

        # Multi-part queries
        if re.search(r'(?:and |also |then |first|second|third|finally)', q_lower):
            if adjusted.value == "low":
                adjusted = Complexity.MEDIUM
            elif adjusted.value == "medium":
                adjusted = Complexity.HIGH
            adjustments.append("multi_part")

        # Nested questions
        if re.search(r'\?.*\?', q):
            if adjusted.value == "low":
                adjusted = Complexity.MEDIUM
            adjustments.append("nested_questions")

        # Code-related queries
        if extraction.get("code_snippet") or re.search(r'(?:code|function|script|debug)', q_lower):
            adjusted = Complexity.max(adjusted, Complexity.HIGH)
            adjustments.append("code_query")

        # Research-related queries
        if extraction.get("mode") == "research" or any(kw in q_lower for kw in ["research", "study", "find out"]):
            adjusted = Complexity.max(adjusted, Complexity.DEEP)
            adjustments.append("research_query")

        # Vision-related queries
        if extraction.get("vision"):
            if adjusted.value in ("low", "medium"):
                adjusted = Complexity.MEDIUM
            adjustments.append("vision_query")

        # Automation-related queries
        if extraction.get("mode") == "automation":
            if adjusted.value == "low":
                adjusted = Complexity.MEDIUM
            adjustments.append("automation_query")

        return adjusted

    def _calculate_reasoning_allocations(
        self, complexity: Complexity, extraction: Dict[str, Any]
    ) -> list[dict]:
        """Calculate how reasoning resources should be allocated."""
        allocations: list[dict] = []

        # Base reasoning depth based on complexity
        if complexity == Complexity.LOW:
            allocations.append({
                "phase": "direct_response",
                "duration": "minimal",
                "llm_calls": 1,
                "description": "Direct answer without iterative reasoning",
            })
        elif complexity == Complexity.MEDIUM:
            allocations.extend([
                {
                    "phase": "intent_clarification",
                    "duration": "short",
                    "llm_calls": 1,
                    "description": "Clarify user intent if needed",
                },
                {
                    "phase": "direct_response",
                    "duration": "short",
                    "llm_calls": 1,
                    "description": "Provide direct answer",
                },
            ])
        elif complexity == Complexity.HIGH:
            allocations.extend([
                {
                    "phase": "intent_clarification",
                    "duration": "short",
                    "llm_calls": 1,
                    "description": "Clarify user intent",
                },
                {
                    "phase": "decompose_task",
                    "duration": "medium",
                    "llm_calls": 1,
                    "description": "Break down complex task into sub-tasks",
                },
                {
                    "phase": "reasoning",
                    "duration": "long",
                    "llm_calls": 2,
                    "description": "Iterative reasoning with ReAct pattern",
                },
                {
                    "phase": "direct_response",
                    "duration": "medium",
                    "llm_calls": 1,
                    "description": "Provide comprehensive answer",
                },
            ])
        elif complexity == Complexity.DEEP:
            allocations.extend([
                {
                    "phase": "intent_clarification",
                    "duration": "short",
                    "llm_calls": 1,
                    "description": "Clarify user intent",
                },
                {
                    "phase": "decompose_task",
                    "duration": "medium",
                    "llm_calls": 1,
                    "description": "Break down complex task into sub-tasks",
                },
                {
                    "phase": "deep_reasoning",
                    "duration": "very_long",
                    "llm_calls": 3,
                    "description": "Deep iterative reasoning with multiple ReAct loops",
                },
                {
                    "phase": "verification",
                    "duration": "medium",
                    "llm_calls": 1,
                    "description": "Verify reasoning and results",
                },
                {
                    "phase": "synthesis",
                    "duration": "medium",
                    "llm_calls": 1,
                    "description": "Synthesize final comprehensive answer",
                },
            ])

        # Task-specific adjustments
        if extraction.get("vision"):
            allocations.append({
                "phase": "vision_processing",
                "duration": "short",
                "llm_calls": 1,
                "description": "Process visual input if present",
            })

        if extraction.get("code_snippet"):
            allocations.append({
                "phase": "code_analysis",
                "duration": "medium",
                "llm_calls": 1,
                "description": "Analyze or generate code",
            })

        if extraction.get("mode") == "research":
            allocations.append({
                "phase": "web_research",
                "duration": "very_long",
                "llm_calls": 2,
                "description": "Search and retrieve information",
            })

        if extraction.get("mode") == "automation":
            allocations.append({
                "phase": "deterministic_dispatch",
                "duration": "minimal",
                "llm_calls": 0,
                "description": "Direct API calls, no LLM reasoning needed",
            })

        return allocations

    def _estimate_tokens(self, query: str, complexity: Complexity, extraction: Dict[str, Any]) -> int:
        """Estimate token requirements for the query."""
        base_tokens = max(64, len(query) // 4)

        complexity_multiplier: dict[Complexity, float] = {
            Complexity.LOW: 1.0,
            Complexity.MEDIUM: 1.5,
            Complexity.HIGH: 2.5,
            Complexity.DEEP: 4.0,
        }

        multiplier = complexity_multiplier.get(complexity, 1.5)

        # Vision adds token cost
        if extraction.get("vision"):
            multiplier *= 1.5

        # Code adds token cost
        if extraction.get("code_snippet"):
            multiplier *= 1.3

        # Research adds significant token cost
        if extraction.get("mode") == "research":
            multiplier *= 2.5

        estimated = int(base_tokens * multiplier)
        return max(64, estimated)

    def _determine_model_preference(self, complexity: Complexity, extraction: Dict[str, Any]) -> str:
        """Determine the preferred model category based on complexity and task type."""
        if complexity == Complexity.DEEP:
            return "large"
        elif complexity == Complexity.HIGH:
            return "medium"
        elif complexity == Complexity.MEDIUM:
            if extraction.get("vision") or extraction.get("mode") == "research":
                return "medium"
            return "efficient"
        else:  # LOW
            if extraction.get("mode") == "automation":
                return "efficient"
            return "efficient"

    def _record_analysis(self, analysis: dict) -> None:
        """Record complexity analysis for telemetry."""
        self._analysis_history.append(analysis)
        if len(self._analysis_history) > self._max_history:
            self._analysis_history = self._analysis_history[-self._max_history:]

    def get_analysis_history(self) -> list[dict]:
        """Get complexity analysis history."""
        return list(self._analysis_history)

    def get_stats(self) -> dict:
        """Get complexity analysis statistics."""
        if not self._analysis_history:
            return {"total_analyses": 0}

        complexity_counts: dict[str, int] = {}
        intent_counts: dict[str, int] = {}

        for analysis in self._analysis_history:
            complexity = analysis.get("complexity", {}).value if isinstance(analysis.get("complexity"), Complexity) else "unknown"
            intent = analysis.get("intent", {}).value if isinstance(analysis.get("intent"), Intent) else "unknown"
            complexity_counts[complexity] = complexity_counts.get(complexity, 0) + 1
            intent_counts[intent] = intent_counts.get(intent, 0) + 1

        return {
            "total_analyses": len(self._analysis_history),
            "complexity_distribution": complexity_counts,
            "intent_distribution": intent_counts,
        }