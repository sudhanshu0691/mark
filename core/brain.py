"""
Mark decision brain.

For a multi-step goal ("install VS Code, then open it and tell Rahul on WhatsApp")
this runs a plan -> act -> VERIFY -> re-plan loop using the configured provider (core/llm.py,
Ollama by default) using the same tools Mark has.

The verify step is enforced here in code (core/verify.py), not left to the model:
every world-changing tool call is independently checked (file exists / window open /
text on screen / vision look at a screenshot). A failed check turns [DONE] into
[FAILED]; an inconclusive one stays [UNVERIFIED]; and a final report of
"RESULT: SUCCESS" is downgraded to PARTIAL unless everything was verified.
"""
from __future__ import annotations

import json
import re
from typing import Callable

from core import llm, risk
from core import verify as _verify

MAX_STEPS_DEFAULT = 10
_EXCLUDE = {"run_goal", "shutdown_mark", "screen_process", "close_camera"}

SYSTEM = """You are the decision brain of a Windows PC assistant. You get a GOAL and must achieve it using tools.
Work in a loop: decide the next action, call ONE tool, read its result, then decide again.

Hard rules:
1. Every tool result starts with [DONE], [UNVERIFIED] or [FAILED]. Only [DONE] counts as achieved.
2. If a step is [FAILED], read the REASON and choose a DIFFERENT approach (another tool, another path, install the missing app,
   search first, etc.). Do not repeat the identical call. If there is no sensible alternative, stop and report.
3. If [UNVERIFIED], do not repeat the action or ask for approval; report that execution occurred but its effect is uncertain. A read-only check may help.
4. Destructive actions (permanent delete, shutdown, formatting, sending money, sending messages to people the user did not name)
   must NOT be taken unless the goal explicitly asks for them.
5. Never invent results. Never say something was done unless a tool returned [DONE] for it.
6. When finished or stuck, reply with plain text (no tool call) in this format:
   RESULT: SUCCESS | PARTIAL | FAILED
   DONE: <what is verified done>
   NOT DONE: <what did not happen and the exact reason>
Reply in the user's language (Hinglish is fine)."""


def _first_line(text: str) -> str:
    return text.splitlines()[0] if text else ""


def _apply_verdict(out: str, v: "_verify.Verdict") -> str:
    """Merge the independent verdict into the tool's own answer."""
    first = _first_line(out)
    if v.status == _verify.FAIL and first.startswith(("[DONE]", "[UNVERIFIED]")):
        body = out.split("\n", 1)[1] if "\n" in out else ""
        return ("[FAILED] The tool reported success but the independent check disagrees.\n"
                f"REASON: {v.detail}\nTOOL SAID: {first[:200]}\n{body}")
    if v.status == _verify.PASS:
        out = out.replace("[UNVERIFIED]", "[DONE]", 1) if first.startswith("[UNVERIFIED]") else out
        return out + f"\nVERIFIED ({v.method}): {v.detail}"
    if v.status == _verify.SKIP and v.method != "none":
        if first.startswith("[DONE]"):
            out = out.replace("[DONE]", "[UNVERIFIED]", 1)
        return out + f"\nVERIFY NOTE ({v.method}): {v.detail}"
    return out


@llm.providers.task_budget()
def run_goal(goal: str, api_key: str = "", declarations: list[dict] | None = None,
             dispatch: Callable[[str, dict], str] | None = None, log: Callable[[str], None] = print,
             max_steps: int = MAX_STEPS_DEFAULT, context: str = "",
             visual_checkpoints: bool = False,
             checkpoint: Callable[[str], str] | None = None,
             cancelled: Callable[[], bool] | None = None,
             verifier: "_verify.Deps | None" = None,
             chat: Callable[..., dict] | None = None, history: list[dict] | None = None) -> str:
    """`api_key` is accepted for backward compatibility and ignored (credentials are configured separately)."""
    assert dispatch is not None, "dispatch is required"
    chat = chat or llm.chat_tools
    deps = verifier or _verify.default_deps(dispatch)
    decls = [d for d in (declarations or []) if d.get("name") not in _EXCLUDE]
    from core.plugin_router import plan as plugin_plan
    workflow = plugin_plan(goal, decls)
    if workflow:
        log("[plugin router] " + workflow)
    system = SYSTEM + (f"\n\n{workflow}" if workflow else "") + (f"\n\nCONTEXT:\n{context}" if context else "")
    messages: list[dict] = [{**item, "_local_only": True} for item in (history or [])] + [{"role": "user", "content": f"GOAL: {goal}"}]
    trace: list[str] = []
    recoveries: list[str] = []
    checkpoints: list[str] = []
    verdicts: list[str] = []
    unverified_steps: list[str] = []
    contradicted: dict[str, str] = {}      # tool -> why the checker disagreed (cleared once it passes)
    used_gui = False

    def suffix() -> str:
        lines = ["\nEXECUTION TRACE:"]
        lines.extend(f"- step {i}: {entry}" for i, entry in enumerate(trace, 1))
        if verdicts:
            lines.append("INDEPENDENT VERIFICATION:")
            lines.extend(f"- {v}" for v in verdicts[-10:])
        if recoveries:
            lines.append("RECOVERY HISTORY:")
            lines.extend(f"- {entry}" for entry in recoveries[-8:])
        if checkpoints:
            lines.append("VISUAL CHECKPOINTS:")
            lines.extend(f"- {entry}" for entry in checkpoints[-8:])
        return "\n".join(lines)

    last_calls: list[str] = []

    for step in range(1, max_steps + 1):
        if cancelled and cancelled():
            return "RESULT: CANCELLED\nDONE: " + "; ".join(t for t in trace if "[DONE]" in t)[:600] + \
                   "\nNOT DONE: user cancelled the task." + suffix()
        try:
            msg = chat(messages, decls, system=system)
        except llm.LLMUnavailable as e:
            return (f"RESULT: FAILED\nDONE: {'; '.join(trace) or 'nothing'}\n"
                    f"NOT DONE: model request failed ({e}){suffix()}")
        except Exception as e:
            return (f"RESULT: FAILED\nDONE: {'; '.join(trace) or 'nothing'}\n"
                    f"NOT DONE: brain error {type(e).__name__}: {e}{suffix()}")
        calls = msg.get("tool_calls") or []
        assistant_message = {"role": "assistant", "content": msg.get("content", "")}
        if calls:
            assistant_message["tool_calls"] = calls
        if "_gemini_content" in msg:
            assistant_message["_gemini_content"] = msg["_gemini_content"]
        messages.append(assistant_message)
        if not calls:
            text = (msg.get("content") or "").strip()
            report = text or "RESULT: PARTIAL\nNOT DONE: brain ended without a report"
            return _gate_report(report, goal, deps, used_gui, unverified_steps, contradicted, verdicts, log) + suffix()

        for tc in calls:
            if cancelled and cancelled():
                return "RESULT: CANCELLED\nDONE: " + "; ".join(t for t in trace if "[DONE]" in t)[:600] + \
                       "\nNOT DONE: user cancelled the task." + suffix()
            fn = tc.get("function", {})
            name, args = fn.get("name", ""), fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {}
            if not isinstance(args, dict):
                return "RESULT: FAILED\nNOT DONE: invalid tool argument object."
            args = dict(args)
            sig = name + json.dumps(args, sort_keys=True, default=str)
            log(f"[brain {step}/{max_steps}] {name}")
            duplicate = sig in last_calls and name in _verify.CHECKED_TOOLS
            if duplicate:
                out = "[UNVERIFIED] This operation was already attempted. Inspect the result before requesting another action."
            elif last_calls.count(sig) >= 2:
                out = "[FAILED] You already tried this exact call twice. Choose a different approach or stop and report."
            else:
                try:
                    out = dispatch(name, args)
                except Exception as e:
                    out = f"[FAILED] tool crashed\nREASON: {type(e).__name__}: {e}"
            last_calls.append(sig)
            if name in _verify.GUI_TOOLS:
                used_gui = True

            # ---- enforced verification (not optional for the model) ----
            if not duplicate and out.startswith(("[DONE]", "[UNVERIFIED]")) and name in _verify.CHECKED_TOOLS:
                try:
                    v = _verify.verify_step(name, args, deps, goal=goal)
                except Exception as e:
                    v = _verify.Verdict(_verify.SKIP, "none", f"verifier crashed: {type(e).__name__}: {e}")
                verdicts.append(f"{name}: {v.line()}")
                log(f"[brain {step}] verify -> {v.line()[:120]}")
                out = _apply_verdict(out, v)
                if v.status == _verify.FAIL:
                    contradicted[name] = v.detail
                elif v.status == _verify.PASS:
                    contradicted.pop(name, None)
                if not out.startswith("[DONE]") and not out.startswith("[FAILED]"):
                    unverified_steps.append(f"{name}: {_first_line(out)[:100]}")
            if out.startswith("[UNVERIFIED]") and name not in _verify.CHECKED_TOOLS:
                unverified_steps.append(f"{name}: {_first_line(out)[:100]}")
            if out.startswith(("[CANCELLED]", "[FAILED]")):
                contradicted[name] = _first_line(out)[:120]
            elif out.startswith("[DONE]"):
                contradicted.pop(name, None)
            first = _first_line(out)
            trace.append(f"{name}: {first[:80]}")
            log(f"[brain {step}] -> {first[:110]}")
            if out.startswith("[FAILED]") and last_calls.count(sig) == 1:
                recoveries.append(f"{name}: first attempt failed; planner selected a recovery path")
            if visual_checkpoints and out.startswith("[DONE]") and checkpoint:
                try:
                    r = checkpoint(name)
                    checkpoints.append(f"{name}: {r[:160]}")
                    log(f"[brain {step}] checkpoint -> {r[:110]}")
                except Exception as e:
                    checkpoints.append(f"{name}: checkpoint failed ({type(e).__name__}: {e})")
            messages.append({"role": "tool", "tool_name": name, "content": out[:2400]})

    return ("RESULT: PARTIAL\nDONE: " + "; ".join(t for t in trace if "[DONE]" in t)[:600] +
            f"\nNOT DONE: stopped after {max_steps} steps without finishing the goal. Steps: " + " | ".join(trace[-6:]) +
            suffix())


def _gate_report(report: str, goal: str, deps: "_verify.Deps", used_gui: bool,
                 unverified_steps: list[str], contradicted: dict[str, str], verdicts: list[str],
                 log: Callable[[str], None]) -> str:
    """Never let 'SUCCESS' through unless the evidence backs it."""
    if not re.search(r"RESULT:\s*SUCCESS", report, re.I):
        return report
    reasons: list[str] = []
    for tool, why in contradicted.items():
        reasons.append(f"{tool} was checked and the check failed ({why})")
    if unverified_steps:
        reasons.append("these steps were not independently verified: " + "; ".join(unverified_steps[-4:]))
    if used_gui:
        fv = _verify.final_screen_check(goal, deps)
        verdicts.append(f"final screen check: {fv.line()}")
        log(f"[brain] final screen check -> {fv.line()[:120]}")
        if fv.status == _verify.FAIL:
            reasons.append(f"the final screen does not show the goal done ({fv.detail})")
    if not reasons:
        return report
    return re.sub(r"RESULT:\s*SUCCESS", "RESULT: PARTIAL", report, count=1, flags=re.I) + \
        "\nNOT VERIFIED: " + " | ".join(reasons)
