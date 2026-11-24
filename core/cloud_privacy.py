"""Best-effort credential redaction for cloud text payloads.

Not a complete DLP or image-redaction system. Never mutates local history.
"""
from __future__ import annotations
import re
import json

_PATTERNS = (
    re.compile(r"(?i)(\b(?:password|passwd|api[_ -]?key|access[_ -]?token|secret)\b\s*[:=]\s*)([^\s,;]+)"),
    re.compile(r"(?i)(\bBearer\s+)([A-Za-z0-9._~+/-]+)"),
    re.compile(r"\b(?:gsk_[A-Za-z0-9_-]{16,}|AIza[A-Za-z0-9_-]{30,}|sk-[A-Za-z0-9_-]{16,})\b"),
)

def redact_text(text):
    for pattern in _PATTERNS:
        text = pattern.sub(lambda m: (m.group(1) if m.lastindex else "") + "[REDACTED]", text)
    return text

_SENSITIVE = re.compile(r"^(?:password|passwd|passphrase|api[_ -]?key|access[_ -]?token|refresh[_ -]?token|client[_ -]?secret|secret|authorization|private[_ -]?key)$", re.I)

def redact_messages(messages, cfg):
    if not cfg.get("privacy", {}).get("redact_sensitive_before_cloud", True):
        return messages
    def clean(value):
        if isinstance(value, str):
            return redact_text(value)
        if isinstance(value, list):
            return [clean(item) for item in value]
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                if _SENSITIVE.fullmatch(str(key)):
                    result[key] = "[REDACTED]"
                elif key == "arguments" and isinstance(item, str):
                    try:
                        result[key] = json.dumps(clean(json.loads(item)), ensure_ascii=False)
                    except (ValueError, TypeError):
                        result[key] = redact_text(item)
                else:
                    result[key] = clean(item)
            return result
        return value
    # Native Gemini content carries thought signatures. Copy its typed structure
    # while redacting textual and function-call data, leaving binary metadata intact.
    def native(value):
        if not hasattr(value, "model_dump") or not hasattr(type(value), "model_validate"):
            return value
        data = value.model_dump(exclude_none=True)
        return type(value).model_validate(clean(data))
    return [{key: (native(value) if key == "_gemini_content" else clean(value))
             if key in {"content", "tool_calls", "_gemini_content"} else value
             for key, value in message.items()} for message in messages]
