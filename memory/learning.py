"""Conservative local learning of explicit, stable user preferences."""
import re
from core.cloud_privacy import redact_text

_RULES = (
    (r"(?:my preferred (?:reply )?language is|i prefer (?:replies|responses) in|reply to me in|mujhe jawab do|(?:मुझे )?जवाब दो)\s+(english|hindi|hinglish)", "preferences", "reply_language"),
    (r"(english|hindi|hinglish|हिंदी|अंग्रेजी|हिंग्लिश)\s+(?:mein (?:jawab|reply) do|में जवाब दो)", "preferences", "reply_language"),
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
                db.upsert_fact(category, key, {"हिंदी": "hindi", "अंग्रेजी": "english",
                                               "हिंग्लिश": "hinglish"}.get(value, value.casefold()), source="user_explicit")
                return True
    return False


_EXPLICIT = re.compile(
    r"(?:remember|yaad rakho|याद रखो)\s+([\w -]{1,48})\s*[:=]\s*(.{1,380})", re.I
)
_FORGET = re.compile(r"(?:forget|bhool jao|भूल जाओ)\s+([\w -]{1,48})", re.I)


def handle_memory_command(text):
    """Handle deliberately structured user-authored notes without cloud inference."""
    from memory import db

    normalized = text.strip()
    match = _EXPLICIT.fullmatch(normalized)
    if match:
        key, value = match.groups()
        key = " ".join(key.casefold().split())
        error = db.validate_fact(key, value)
        if error:
            return f"[FAILED] {error}"
        if re.search(r"(?i)\b(?:ssn|aadhaar|aadhar|medical|diagnosis|passport|bank account)\b", key + " " + value):
            return "[FAILED] Store sensitive personal details using a trusted local record."
        db.upsert_fact("notes", key, value, source="user_explicit")
        return "[DONE] Note saved locally."
    match = _FORGET.fullmatch(normalized)
    if match:
        key = " ".join(match[1].casefold().split())
        if db.delete_fact("notes", key):
            return "[DONE] Note removed locally."
        return "[UNVERIFIED] No saved note matched that key."
    return None
