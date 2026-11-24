"""
Honest action results.

Every action that changes the outside world returns an ActionResult with one of
three statuses.  The assistant is only allowed to say "done" for DONE.

  DONE        - the action ran AND we verified the effect (file exists, window shows
                the message, package is listed by winget, ...).
  UNVERIFIED  - the action ran (no error) but the effect could not be confirmed.
  FAILED      - the action did not happen.  `reason` says exactly why.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

DONE = "DONE"
UNVERIFIED = "UNVERIFIED"
FAILED = "FAILED"

_RULES = {
    DONE: "Verified. You may tell the user it is done.",
    UNVERIFIED: ("Action was executed but NOT confirmed. Do NOT say it is done. "
                 "Say it was attempted and could not be verified, and say why."),
    FAILED: ("Action did NOT happen. Do NOT say it is done. "
             "Tell the user it failed and give the REASON in plain words."),
}


@dataclass
class ActionResult:
    status: str
    message: str
    reason: str = ""
    evidence: str = ""
    data: Any = field(default=None, repr=False)

    @property
    def ok(self) -> bool:
        return self.status == DONE

    def to_llm(self) -> str:
        parts = [f"[{self.status}] {self.message}"]
        if self.reason:
            parts.append(f"REASON: {self.reason}")
        if self.evidence:
            parts.append(f"EVIDENCE: {self.evidence}")
        parts.append(f"RULE: {_RULES[self.status]}")
        return "\n".join(parts)

    def __str__(self) -> str:  # so legacy code that prints/returns str() keeps working
        return self.to_llm()


def done(message: str, evidence: str = "", data: Any = None) -> ActionResult:
    return ActionResult(DONE, message, evidence=evidence, data=data)


def unverified(message: str, reason: str = "", evidence: str = "") -> ActionResult:
    return ActionResult(UNVERIFIED, message, reason=reason, evidence=evidence)


def failed(message: str, reason: str) -> ActionResult:
    return ActionResult(FAILED, message, reason=reason)


_FAIL_RE = re.compile(
    r"\b(could not|couldn't|cannot|can't|failed|failure|error|not found|not installed|"
    r"denied|unable|no such|invalid|unknown|missing|not available|refused|timed out|timeout)\b",
    re.I,
)


def coerce_side_effect(tool: str, value: Any) -> str:
    """
    Wrap the answer of a LEGACY action (which only returns free text) so the model
    cannot mistake an unconfirmed action for a confirmed one.
    """
    if isinstance(value, ActionResult):
        return value.to_llm()
    text = "" if value is None else str(value).strip()
    if text.startswith(("[DONE]", "[FAILED]", "[UNVERIFIED]")):
        return text
    if not text:
        return unverified(f"{tool} returned no confirmation.",
                          reason="The action gave no output, so its effect is unknown.").to_llm()
    if _FAIL_RE.search(text):
        return failed(f"{tool} did not complete.", reason=text[:400]).to_llm()
    return unverified(f"{tool} ran: {text[:300]}",
                      reason="This action does not check its own result.").to_llm()
