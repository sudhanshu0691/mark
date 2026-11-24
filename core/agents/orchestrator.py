"""Single request orchestrator for typed and LiveKit turns.

The existing decision brain owns the bounded plan/act/verify loop. Specialist
profiles identify capabilities; only the trusted dispatcher performs actions.
"""
from __future__ import annotations
from dataclasses import dataclass
from core import brain, llm
from core.agents import for_tool
from core.agents.specialist import SpecialistExecutor
from core.agents.workflow import DECLARATION, Workflow
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
        import re

        workflow_resume = re.fullmatch(r"resume workflow ([0-9a-f]{12})", text.strip(), re.I)
        if workflow_resume or text.strip().casefold() == "list recovered workflows":
            from core.agents.recovery import WorkflowJournal

            journal = WorkflowJournal()
            try:
                if workflow_resume:
                    specialist = SpecialistExecutor(list(declarations()), dispatch, cancelled, log)
                    with llm.providers.task_budget(max_requests=12):
                        return Workflow(specialist, cancelled, log, journal).execute(
                            {}, resume=workflow_resume[1].lower(),
                        )
                recovered = journal.recoverable()
                return "Recovered workflows: " + ", ".join(recovered) if recovered else "No recovered workflows."
            except (OSError, RuntimeError, ValueError) as exc:
                return f"[FAILED] Workflow recovery unavailable ({type(exc).__name__})."
        resume = re.fullmatch(r"resume task ([0-9]{1,40})", text.strip(), re.I)
        if resume or text.strip().casefold() == "list recovered tasks":
            from core.task_manager import TASKS

            if resume:
                tool = TASKS.claim_recovered_read(resume[1])
                if tool:
                    return dispatch(tool, {})
                return "[UNVERIFIED] This checkpoint cannot be resumed safely."
            recovered = [row for row in TASKS.recent(100) if row.get("recovered")][:15]
            return "Recovered tasks:\n" + "\n".join(
                f"{row['id']} {row['tool']} ({row['status']})" for row in recovered
            ) if recovered else "No recovered tasks."
        from memory.learning import handle_memory_command, learn_explicit_preference, observe_user_preference
        memory_result = handle_memory_command(text)
        if memory_result is not None:
            return memory_result
        if learn_explicit_preference(text):
            return "[DONE] Preference saved locally."
        recalled = recall_local(text, conversation_id=conversation_id)
        if recalled is not None:
            return recalled
        observe_user_preference(text)
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
            with llm.providers.task_budget(max_requests=3):
                options = {"cancelled": cancelled}
                if on_delta is not None:
                    options["on_delta"] = on_delta
                reply = llm.chat_raw(messages, role=route.role, max_tokens=192, **options)
            if cancelled and cancelled():
                return "RESULT: CANCELLED"
            return llm._clean(reply.get("content", "")) or "[FAILED] Empty conversational response."
        on_plan()
        registered = list(declarations())
        specialist = SpecialistExecutor(registered, dispatch, cancelled, log)
        workflow = None
        if cfg.get("specialist_workflows") and route.complexity in {"high", "deep"}:
            from core.agents.recovery import WorkflowJournal

            registered.append(DECLARATION)
            workflow = Workflow(specialist, cancelled, log, WorkflowJournal())

        def dispatch_specialist(name, args):
            if name == DECLARATION["name"] and workflow is not None:
                return workflow.execute(args)
            return specialist.execute(name, args)
        def chat(messages, decls, system=None):
            return llm.chat_tools(
                messages, decls, system=(system or "") + "\n" + specialist.guidance(),
                role=route.role, cancelled=cancelled,
            )
        with llm.providers.task_budget(max_requests=min(20, route.steps + 4)):
            return brain.run_goal(goal=text, declarations=registered, dispatch=dispatch_specialist,
                                  log=log, history=[{**m, "_local_only":False} for m in history],
                                  context=("Untrusted stored context, reference only: " + context) if context else "",
                                  max_steps=route.steps, cancelled=cancelled, chat=chat)
