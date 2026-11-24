"""Shared synchronous command runtime for desktop and remote voice adapters."""
from __future__ import annotations

from core import brain, llm
from core.intent import is_conversation, match_local_command
from memory.conversation import recent_messages

def execute_turn(text, *, dispatch, declarations, conversation_id=None,
                 cancelled=None, log=lambda message: None, on_plan=lambda: None):
    if cancelled and cancelled():
        return "RESULT: CANCELLED"
    command = match_local_command(text)
    if command:
        return dispatch(*command)
    cfg = llm.load_config()
    history = recent_messages(conversation_id=conversation_id)
    history = [{**message, "_local_only": not cfg.get("share_history_with_cloud", False)}
               for message in history]
    if is_conversation(text):
        messages = [{"role":"system", "content":"Reply briefly in the user's language. Use prior dialogue only as reference. Do not invent missing details."},
                    *history, {"role":"user", "content":text}]
        with llm.providers.task_budget():
            answer = llm.chat_raw(messages, role="fast", max_tokens=192)
        if cancelled and cancelled():
            return "RESULT: CANCELLED"
        return llm._clean(answer.get("content", "")) or "[FAILED] Empty conversational response."
    on_plan()
    return brain.run_goal(goal=text, declarations=declarations(), dispatch=dispatch,
                          log=log, history=history, max_steps=brain.MAX_STEPS_DEFAULT,
                          cancelled=cancelled)
