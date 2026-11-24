from __future__ import annotations

from typing import Dict, Any, Optional, Tuple
from enum import Enum
import re
import logging

logger = logging.getLogger(__name__)


class Intent(Enum):
    CONVERSATION = 'conversation'
    COMMAND = 'command'
    CREATIVE = 'creative'
    ANALYSIS = 'analysis'
    CODE = 'code'
    AUTOMATION = 'automation'
    VISION = 'vision'
    VOICE = 'voice'
    RESEARCH = 'research'
    FILE_OP = 'file_op'
    PRODUCTIVITY = 'productivity'
    SYSTEM_MGMT = 'system_mgmt'
    MEMORY_QUERY = 'memory_query'
    SETTINGS = 'settings'


class Complexity(Enum):
    LOW = 'low'
    MEDIUM = 'medium'
    HIGH = 'high'
    DEEP = 'deep'


def classify_intent(query: str) -> Tuple[Intent, Complexity, Dict[str, Any]]:
    """Classify user intent from query."""
    if not query or not query.strip():
        return Intent.CONVERSATION, Complexity.LOW, {}
    q = query.strip()
    q_lower = q.lower()
    extraction: Dict[str, Any] = {}
    code_patterns = re.findall(r'[`"]([^`"]+)[`"]', q)
    if code_patterns:
        extraction["code_snippet"] = code_patterns[0]
    if any(kw in q_lower for kw in ["email", "mail", "send email"]):
        extraction["target"] = "email"
    if any(kw in q_lower for kw in ["calendar", "schedule", "appointment", "meeting"]):
        extraction["target"] = "calendar"
    if any(kw in q_lower for kw in ["research", "search", "find", "lookup"]):
        extraction["mode"] = "research"
    if re.search(r'(?:what|how|describe|identify|what is)\s+(?:this |that |the )?(?:image|picture|photo|screen)', q_lower):
        extraction["vision"] = True
    if any(kw in q_lower for kw in ["system", "settings", "preferences"]):
        extraction["target"] = "system"
    if any(kw in q_lower for kw in ["automate", "click", "type", "open", "run", "execute"]):
        extraction["mode"] = "automation"
    if extraction.get("vision"):
        return Intent.VISION, Complexity.MEDIUM, extraction
    if re.search(r'(?:code|program|function|script|debug|fix)', q_lower):
        return Intent.CODE, Complexity.HIGH, extraction
    if extraction.get("mode") == "automation" or re.search(r'(?:click|type|open |run |execute )', q_lower):
        return Intent.AUTOMATION, Complexity.MEDIUM, extraction
    if extraction.get("mode") == "research" or any(kw in q_lower for kw in ["research", "study", "analyze"]):
        return Intent.RESEARCH, Complexity.DEEP, extraction
    if re.search(r'(?:open |read |save |delete |move |copy )', q_lower):
        return Intent.FILE_OP, Complexity.MEDIUM, extraction
    if extraction.get("target") == "email":
        return Intent.PRODUCTIVITY, Complexity.MEDIUM, extraction
    if extraction.get("target") == "calendar":
        return Intent.PRODUCTIVITY, Complexity.MEDIUM, extraction
    if extraction.get("target") == "system":
        return Intent.SYSTEM_MGMT, Complexity.LOW, extraction
    greeting_patterns = [r'^(hello|hi|hey|hola|greetings)[!,]?\s*$', r'^(how are you|what.s.up|how.do.do)[?,]?\s*$']
    if any(re.match(p, q_lower) for p in greeting_patterns):
        return Intent.CONVERSATION, Complexity.LOW, extraction
    return Intent.COMMAND, Complexity.MEDIUM, extraction


def assess_complexity(query: str, intent: Intent, extraction: Dict[str, Any]) -> Complexity:
    """Assess query complexity."""
    q = query.strip()
    q_lower = q.lower()
    base_complexity = {
        Intent.CONVERSATION: Complexity.LOW,
        Intent.COMMAND: Complexity.MEDIUM,
        Intent.CREATIVE: Complexity.MEDIUM,
        Intent.ANALYSIS: Complexity.HIGH,
        Intent.CODE: Complexity.HIGH,
        Intent.AUTOMATION: Complexity.MEDIUM,
        Intent.VISION: Complexity.MEDIUM,
        Intent.VOICE: Complexity.LOW,
        Intent.RESEARCH: Complexity.DEEP,
        Intent.FILE_OP: Complexity.MEDIUM,
        Intent.PRODUCTIVITY: Complexity.MEDIUM,
        Intent.SYSTEM_MGMT: Complexity.LOW,
        Intent.MEMORY_QUERY: Complexity.MEDIUM,
        Intent.SETTINGS: Complexity.LOW,
    }
    complexity = base_complexity.get(intent, Complexity.MEDIUM)
    q_len = len(q)
    if q_len > 200:
        if complexity.value not in ('deep',):
            complexity = Complexity.HIGH
    if re.search(r'(?:and |also |then |after that|first.second|multiple)', q_lower):
        if complexity.value == 'low':
            complexity = Complexity.MEDIUM
        elif complexity.value == 'medium':
            complexity = Complexity.HIGH
    if re.search(r'(?:calculate |compute |analyze |explain in detail|step.by.step)', q_lower):
        if complexity.value == 'low':
            complexity = Complexity.MEDIUM
        elif complexity.value == 'medium':
            complexity = Complexity.HIGH
    if intent == Intent.CODE:
        complexity = Complexity.HIGH
    if intent == Intent.RESEARCH:
        complexity = Complexity.DEEP
    return complexity


def model_role(intent: Intent, complexity: Complexity, *, agentic: bool = False) -> str:
    """Canonical model-role policy; vision actions first select a tool."""
    if intent == Intent.CODE:
        return "code"
    if intent == Intent.VISION and not agentic:
        return "vision"
    if complexity in (Complexity.HIGH, Complexity.DEEP):
        return "planning"
    if intent == Intent.CONVERSATION and not agentic:
        return "fast"
    return "tool" if agentic else "text"


def route_for_intent(intent: Intent, complexity: Complexity, available_models: Dict[str, str]) -> Tuple[str, Dict[str, Any]]:
    role = model_role(intent, complexity)
    model = available_models.get(role) or available_models.get("text", "")
    return model, {"intent": intent.value, "complexity": complexity.value,
                   "role": role, "model": model,
                   "reasoning_depth": "deep" if complexity in (Complexity.HIGH, Complexity.DEEP) else "standard"}
