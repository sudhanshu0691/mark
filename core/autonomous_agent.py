"""Autonomous Agent for MARK."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from core import llm
from core import intent as _intent
from core import risk


@dataclass
class AgentStep:
    step: int
    action: str
    tool: str
    args: dict
    result: str
    verified: bool
    confidence: float

def analyze_requirements(goal: str) -> dict:
    """Analyze a goal before planning: break into steps, identify risks, flag ambiguities."""
    prompt = (
        f"GOAL: {goal}\n\n"
        "Analyze this goal and return ONLY valid JSON:\n"
        "{\n"
        "  \"subtasks\": [\"step 1\", \"step 2\", ...],\n"
        "  \"risk_level\": \"read | write | destructive | external\",\n"
        "  \"needs_confirmation\": true/false,\n"
        "  \"ambiguous_points\": [\"what is unclear\"],\n"
        "  \"clarification_question\": \"question to ask user or null\",\n"
        "  \"estimated_tools\": [\"tool1\", \"tool2\"]\n"
        "}\n"
    )
    try:
        resp = llm.generate(prompt, role="fast", temperature=0.1, max_tokens=512)
        text = (resp or "").strip()
        text = re.sub(r"^```json\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        return json.loads(text)
    except Exception:
        return {
            "subtasks": [goal],
            "risk_level": risk.READ_ONLY,
            "needs_confirmation": False,
            "ambiguous_points": [],
            "clarification_question": None,
            "estimated_tools": [],
        }


async def classify_and_gate(goal: str, tool_name: str, args: dict) -> tuple:
    """Classify intent and determine if confirmation is needed."""
    it = await _intent.classify_intent(goal)
    risk_level = risk.classify(tool_name, args)
    needs_confirm = risk.requires_confirmation(tool_name, args) or it.requires_confirmation
    return risk_level, needs_confirm, it.clarification
def verify_step_result(tool: str, args: dict, result: str, goal: str = "") -> tuple:
    """Verify a tool step result independently."""
    from core import verify as _verify
    deps = _verify.Deps()
    try:
        v = _verify.verify_step(tool, args, deps, goal=goal)
        return v.ok, v.line()
    except Exception as e:
        return False, f"verification error: {e}"


def run_verified_goal(goal: str, dispatch=None, max_steps: int = 10) -> AgentResult:
    """Execute a goal with plan -> act -> verify -> self-correct loop."""
    result = AgentResult(success=False)
    analysis = analyze_requirements(goal)
    if analysis.get("clarification_question"):
        result.final_message = analysis["clarification_question"]
        result.assumptions.append("Goal had ambiguous points - clarification requested")
        return result
    if analysis.get("needs_confirmation"):
        result.final_message = (
            f"This goal involves {analysis.get('risk_level', 'sensitive')} operations. "
            "Please confirm before I proceed."
        )
        result.assumptions.append("Confirmation required for sensitive operations")
        return result
    from core import brain as _brain
    try:
        report = _brain.run_goal(
            goal=goal,
            max_steps=max_steps,
            log=lambda s: None,
            dispatch=dispatch,
        )
        result.final_message = report
        result.success = "RESULT: SUCCESS" in report
    except Exception as e:
        result.final_message = f"Agent execution failed: {e}"
    return result


@dataclass
class AgentResult:
    success: bool
    steps: list[AgentStep] = field(default_factory=list)
    final_message: str = ""
    evidence: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
