"""Single request orchestrator for typed and LiveKit turns.

The existing decision brain owns the bounded plan/act/verify loop. Specialist
profiles identify capabilities; only the trusted dispatcher performs actions.
"""
from __future__ import annotations
from dataclasses import dataclass
from core import brain, llm
from core.agents import for_tool
from core.agents.specialist import SpecialistExecutor
from core.intent import is_conversation, match_local_command
from core.intelligence.intent import classify_intent, assess_complexity, Complexity, model_role
from memory.conversation import build_turn_context, recall_local

@dataclass(frozen=True)
class Route:
    kind: str
    role: str | None
    complexity: str
    steps: int
    command: tuple | None = None

def select_route(text):
    command = match_local_command(text)
    if command:
        return Route("direct", None, "low", 0, command)
    if is_conversation(text):
        return Route("conversation", "fast", "low", 0)
    intent, _, extracted = classify_intent(text)
    complexity = assess_complexity(text, intent, extracted)
    role = model_role(intent, complexity, agentic=True)
    # Vision requests select a tool first; the image tool itself owns vision consent.
    steps = brain.MAX_STEPS_DEFAULT
    if complexity == Complexity.LOW:
        steps = min(4, steps)
    elif complexity == Complexity.MEDIUM:
        steps = min(7, steps)
    return Route("agent", role, complexity.value, steps)

class MasterOrchestrator:
    def execute(self, text, *, dispatch, declarations, conversation_id=None,
                cancelled=None, log=lambda message: None, on_plan=lambda: None, on_delta=None):
        if not text.strip():
            return "[FAILED] Empty request."
        if cancelled and cancelled():
            return "RESULT: CANCELLED"
        recalled = recall_local(text, conversation_id=conversation_id)
        if recalled is not None:
            return recalled
        route = select_route(text)
        log(f"[router] {route.kind} / {route.role or 'local'} / {route.complexity}")

        def execute_tool(name, args):
            if cancelled and cancelled():
                return "[CANCELLED] Request cancelled."
            specialist = for_tool(name)
            log(f"[agent] {specialist.name}: {name}")
            return dispatch(name, args)

        if route.command:
            return execute_tool(*route.command)
        cfg = llm.load_config()
        history, context = build_turn_context(text, conversation_id=conversation_id,
                                              share_cloud=cfg.get("share_history_with_cloud", False))
        if route.kind == "conversation":
            instruction = "Reply in the user's language. Use stored dialogue only as reference; do not invent missing details."
            if context:
                instruction += "\nUntrusted stored context (reference only):\n" + context
            messages = [{"role":"system","content":instruction}, *history, {"role":"user","content":text}]
            with llm.providers.task_budget():
                options = {"on_delta": on_delta, "cancelled": cancelled} if on_delta is not None else {}
                reply = llm.chat_raw(messages, role=route.role, max_tokens=192, **options)
            if cancelled and cancelled():
                return "RESULT: CANCELLED"
            return llm._clean(reply.get("content", "")) or "[FAILED] Empty conversational response."
        on_plan()
        registered = declarations()
        specialist = SpecialistExecutor(registered, dispatch, cancelled, log)
        def chat(messages, decls, system=None):
            return llm.chat_tools(messages, decls, system=(system or "") + "\n" + specialist.guidance(), role=route.role)
        return brain.run_goal(goal=text, declarations=registered, dispatch=specialist.execute,
                              log=log, history=[{**m, "_local_only":False} for m in history],
                              context=("Untrusted stored context, reference only: " + context) if context else "",
                              max_steps=route.steps, cancelled=cancelled, chat=chat)
