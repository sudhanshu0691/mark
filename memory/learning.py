"""Conservative local learning of explicit, stable user preferences."""
import re
from core.cloud_privacy import redact_text

_RULES = (
    (r"(?:my preferred (?:reply )?language is|i prefer (?:replies|responses) in|reply to me in)\s+(english|hindi|hinglish)", "preferences", "reply_language"),
    (r"(?:my preferred editor is|i prefer using)\s+(vs code|visual studio code|pycharm|notepad\+\+|vim|neovim|sublime text)", "preferences", "editor"),
    (r"(?:i prefer|please use)\s+(concise|detailed|brief)\s+(?:answers|replies|responses)", "preferences", "reply_style"),
)
def learn_explicit_preference(text):
    from memory import db
    normalized = text.strip().rstrip(".!").strip()
    for pattern, category, key in _RULES:
        match = re.fullmatch(pattern, normalized, re.I)
        if match:
            value = match.group(1)
            if redact_text(value) == value:
                db.upsert_fact(category, key, value.casefold())
                return True
    return False
