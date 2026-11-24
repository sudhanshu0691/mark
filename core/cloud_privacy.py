"""Best-effort credential redaction for cloud text payloads.

Not a complete DLP or image-redaction system. Never mutates local history.
"""

from __future__ import annotations

import json
import re
from contextlib import contextmanager
from contextvars import ContextVar

_PATTERNS = (
    re.compile(
        r"(?i)(\b(?:password|passwd|api[_ -]?key|access[_ -]?token|secret)\b\s*[:=]\s*)([^\s,;]+)"
    ),
    re.compile(
        r"-----BEGIN (?:[A-Z]+ )*PRIVATE KEY-----.*?-----END (?:[A-Z]+ )*PRIVATE KEY-----",
        re.S,
    ),
    re.compile(
        r"\b(?:gh[pousr]_[A-Za-z0-9_]{30,}|github_pat_[A-Za-z0-9_]{30,}|xox[baprs]-[A-Za-z0-9-]{12,})\b"
    ),
    re.compile(r"(?i)(\bBearer\s+)([A-Za-z0-9._~+/-]+)"),
    re.compile(
        r"\b(?:gsk_[A-Za-z0-9_-]{16,}|AIza[A-Za-z0-9_-]{30,}|sk-[A-Za-z0-9_-]{16,})\b"
    ),
)


def redact_text(text):
    for pattern in _PATTERNS:
        text = pattern.sub(
            lambda m: (m.group(1) if m.lastindex else "") + "[REDACTED]", text
        )
    return text


_SENSITIVE = re.compile(
    r"^(?:password|passwd|passphrase|api[_ -]?key|access[_ -]?token|refresh[_ -]?token|client[_ -]?secret|secret|authorization|private[_ -]?key)$",
    re.I,
)


def redact_messages(messages, cfg):
    def clean(value):
        if isinstance(value, str):
            return prepare_text(value, cfg)
        if isinstance(value, list):
            return [clean(item) for item in value]
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                if _SENSITIVE.fullmatch(str(key)):
                    result[key] = "[REDACTED]"
                elif key == "arguments" and isinstance(item, str):
                    try:
                        result[key] = json.dumps(
                            clean(json.loads(item)), ensure_ascii=False
                        )
                    except (ValueError, TypeError):
                        result[key] = prepare_text(item, cfg)
                else:
                    result[key] = clean(item)
            return result
        return value

    # Native Gemini content carries thought signatures. Copy its typed structure
    # while redacting textual and function-call data, leaving binary metadata intact.
    def native(value):
        if not hasattr(value, "model_dump") or not hasattr(
            type(value), "model_validate"
        ):
            return value
        data = value.model_dump(exclude_none=True)
        return type(value).model_validate(clean(data))

    return [
        {
            key: (native(value) if key == "_gemini_content" else clean(value))
            if key in {"content", "tool_calls", "_gemini_content"}
            else value
            for key, value in message.items()
        }
        for message in messages
    ]


class CloudPolicyError(RuntimeError):
    """The outbound payload is outside the user's configured policy."""


_capture_approved = ContextVar("mark_capture_approved", default=False)


@contextmanager
def approved_capture():
    """Scope screenshot consent to one trusted dispatcher invocation."""
    token = _capture_approved.set(True)
    try:
        yield
    finally:
        _capture_approved.reset(token)


def require_vision(cfg, *, private=True):
    if cfg.get("profile") == "offline" or not cfg.get("allow_cloud_vision", False):
        raise CloudPolicyError("Cloud vision is disabled by policy.")
    if private and not _capture_approved.get():
        raise CloudPolicyError("Cloud analysis requires approval for this capture.")


def prepare_text(text, cfg):
    if cfg.get("profile") == "offline":
        raise CloudPolicyError("Cloud inference is disabled in offline profile.")
    privacy = cfg.get("privacy", {})
    if (
        privacy.get("redact_sensitive_before_cloud", True)
        or privacy.get("never_send_passwords", True)
        or privacy.get("never_send_api_keys", True)
    ):
        return redact_text(text)
    return text


# These operations can return arbitrary local documents, clipboard-like text,
# private correspondence, or OCR. Their full results stay in the dispatcher.
_PRIVATE_TOOL_RESULTS = {
    "file_controller",
    "file_processor",
    "excel",
    "screen_read",
    "computer_control",
    "computer_settings",
    "whatsapp",
    "send_email",
    "message_delivery",
    "code_helper",
    "dev_agent",
    "clipboard",
    "notes",
    "notifications",
}


def private_tool_result(name):
    return name in _PRIVATE_TOOL_RESULTS


def prepare_tool_result(name, response, cfg):
    result = str((response or {}).get("result", ""))
    if private_tool_result(name):
        status = next(
            (
                tag
                for tag in ("[DONE]", "[FAILED]", "[CANCELLED]", "[UNVERIFIED]")
                if result.startswith(tag)
            ),
            "[UNVERIFIED]",
        )
        return {"result": status + " Tool details are available locally."}
    return {
        "result": prepare_text(result, cfg),
        **(
            {"silent": response["silent"]}
            if response and isinstance(response.get("silent"), bool)
            else {}
        ),
    }


def prepare_messages(messages, cfg, role):
    """Validate outbound chat payloads before routing or fallback."""
    result = []
    for message in messages:
        if message.get("images"):
            if role != "vision":
                raise CloudPolicyError("Image attachments require a vision request.")
            require_vision(cfg, private=False)
        if message.get("role") == "tool" and message.get("tool_name"):
            response = prepare_tool_result(
                message["tool_name"], {"result": message.get("content")}, cfg
            )
            message = {**message, "content": response["result"]}
        result.append(message)
    return redact_messages(result, cfg)
