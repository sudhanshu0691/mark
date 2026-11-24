"""Task complexity estimator for MARK's adaptive intelligence router."""

from __future__ import annotations

from typing import Any, Dict, Optional


class ComplexityEstimator:
    """Estimate task complexity to determine required reasoning depth."""

    # Keyword patterns for different complexity levels
    SIMPLE_PATTERNS = {
        "greeting", "small_talk", "question", "request", "info_seek",
        "status", "time", "date", "weather", "simple",
    }

    COMPLEX_PATTERNS = {
        "code", "analysis", "planning", "research", "creative",
        "problem_solving", "multi_step", "decision_making",
        "build", "develop", "design", "implement",
    }

    TOOL_REQUIRING = {
        "web_search", "deep_research", "code_helper", "dev_agent",
        "file_processor", "excel", "database", "ocr",
        "browser_control", "screen_process",
    }

    def estimate(self, user_input: str, context: str = "") -> Dict[str, Any]:
        """Estimate task complexity."""
        text = (user_input or "").lower()
        if not text:
            return {
                "level": "simple",
                "reasoning_depth": 1,
                "estimated_tokens": 100,
                "requires_tools": False,
                "confidence": 1.0,
            }

        # Check for simple patterns
        simple_hits = sum(1 for kw in self.SIMPLE_PATTERNS if kw in text)
        complex_hits = sum(1 for kw in self.COMPLEX_PATTERNS if kw in text)
        tool_hits = sum(1 for kw in self.TOOL_REQUIRING if kw in text)

        if complex_hits > simple_hits and tool_hits > 0:
            return {
                "level": "complex",
                "reasoning_depth": 3,
                "estimated_tokens": 2048,
                "requires_tools": True,
                "confidence": min(1.0, (complex_hits + tool_hits) / 5),
            }
        elif complex_hits > simple_hits:
            return {
                "level": "complex",
                "reasoning_depth": 2,
                "estimated_tokens": 1024,
                "requires_tools": False,
                "confidence": min(1.0, complex_hits / 3),
            }
        elif tool_hits > 0:
            return {
                "level": "medium",
                "reasoning_depth": 2,
                "estimated_tokens": 512,
                "requires_tools": True,
                "confidence": min(1.0, tool_hits / 2),
            }
        else:
            return {
                "level": "simple",
                "reasoning_depth": 1,
                "estimated_tokens": 256,
                "requires_tools": False,
                "confidence": 0.8,
            }