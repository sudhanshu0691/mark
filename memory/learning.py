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
                if key == "editor":
                    value = _EDITORS.get(value.casefold(), value)
                if key == "reply_style" and value.casefold() == "brief":
                    value = "concise"
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
    history = re.fullmatch(r"history (project|note|preference)\s+(.+)", normalized, re.I)
    if history:
        category = {"project": "projects", "note": "notes", "preference": "preferences"}[history[1].lower()]
        key = " ".join(history[2].casefold().strip(" ?").split())
        if category == "preferences":
            key = key.replace(" ", "_")
        revisions = db.fact_history(category, key)
        return "[LOCAL MEMORY] " + (
            "\n".join(f"{updated}  {value} ({source})" for value, source, updated in revisions)
            if revisions else "No prior values."
        )
    recall = re.fullmatch(r"(?:recall|show saved|yaad dilao)\s+(project|note|preference)\s+(.+)", normalized, re.I)
    if recall:
        category = {"project": "projects", "note": "notes", "preference": "preferences"}[recall[1].lower()]
        key = " ".join(recall[2].casefold().strip(" ?").split())
        if category == "preferences":
            key = key.replace(" ", "_")
        fact = db.load_memory_dict().get(category, {}).get(key)
        return "[LOCAL MEMORY] " + fact["value"] if fact else "[LOCAL MEMORY] No matching fact."
    match = _EXPLICIT.fullmatch(normalized)
    if match:
        key, value = match.groups()
        key = " ".join(key.casefold().split())
        category = "projects" if key.startswith("project ") else "notes"
        if category == "projects":
            key = key.removeprefix("project ")
        error = db.validate_fact(key, value)
        if error:
            return f"[FAILED] {error}"
        if re.search(r"(?i)\b(?:ssn|aadhaar|aadhar|medical|diagnosis|passport|bank account)\b", key + " " + value):
            return "[FAILED] Store sensitive personal details using a trusted local record."
        db.upsert_fact(category, key, value, source="user_explicit")
        return "[DONE] Note saved locally."
    match = _FORGET.fullmatch(normalized)
    if match:
        key = " ".join(match[1].casefold().split())
        preference_key = key.replace(" ", "_")
        category = "projects" if key.startswith("project ") else "notes"
        removed = db.delete_fact(category, key.removeprefix("project ") if category == "projects" else key)
        if preference_key in {"editor", "reply_style", "reply_language"}:
            removed = db.delete_fact("preferences", preference_key) or removed
        if removed:
            return "[DONE] Memory removed locally."
        return "[UNVERIFIED] No saved note matched that key."
    return None


# Each candidate is a known enum value. The complete utterance must express
# first-person habitual use, and no raw utterance is stored as evidence.
_OBSERVATIONS = (
    ("editor", r"i (?:usually|always) (?:use|work in) (.+?)(?: for (?:coding|development))?"),
    ("editor", r"main (?:aksar|hamesha) (.+?) (?:use|istemal) karta (?:hu|hoon)"),
    ("editor", r"मैं (?:अक्सर|हमेशा) (.+?) (?:इस्तेमाल|उपयोग) करता हूँ"),
    ("reply_style", r"i (?:usually|always) (?:like|want) (brief|concise|detailed) (?:answers|replies)"),
)
_EDITORS = {"vs code": "vs code", "visual studio code": "vs code",
            "vim": "vim", "neovim": "neovim", "pycharm": "pycharm",
            "sublime text": "sublime text", "notepad++": "notepad++"}


def observe_user_preference(text):
    import hashlib

    from memory import db

    normalized = " ".join(text.casefold().strip(" .!").split())
    if len(normalized) > 180:
        return None
    for key, pattern in _OBSERVATIONS:
        match = re.fullmatch(pattern, normalized)
        if match:
            value = match[1]
            if key == "editor":
                value = _EDITORS.get(value)
            elif value == "brief":
                value = "concise"
            if value:
                evidence = hashlib.sha256(normalized.encode()).hexdigest()
                return db.observe_preference(key, value, evidence)
    return None
