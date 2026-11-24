import json
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
MEMORY_DIR = BASE_DIR / "memory"
CONVERSATION_PATH = MEMORY_DIR / "conversation_history.json"
USER_MEMORY_PATH = MEMORY_DIR / "user_memory.json"
MAX_CONVERSATION_TURNS = 100


def _path(file_path: str | Path | None = None) -> Path:
    return Path(file_path) if file_path else CONVERSATION_PATH


def _user_memory_path(file_path: str | Path | None = None) -> Path:
    return Path(file_path) if file_path else USER_MEMORY_PATH


def save_conversation(history, file_path: str | Path | None = None):
    """Persist recent conversation turns. Delegates to SQLite when no custom path."""
    if file_path is not None:
        target = _path(file_path)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            clean = history if isinstance(history, list) else []
            payload = json.dumps(clean[-MAX_CONVERSATION_TURNS:], indent=2, ensure_ascii=False)
            tmp = target.with_suffix(target.suffix + ".tmp")
            if target.exists():
                try:
                    import shutil
                    shutil.copy2(target, target.with_suffix(target.suffix + ".bak"))
                except Exception:
                    pass
            tmp.write_text(payload, encoding="utf-8")
            import os
            os.replace(tmp, target)
            return f"Conversation history saved to {target}"
        except Exception as e:
            return f"Failed to save conversation: {e}"
    try:
        from memory import db
        if isinstance(history, list) and history:
            last = history[-1]
            if isinstance(last, dict) and "user" in last and "assistant" in last:
                db.append_conversation_turn(str(last["user"]), str(last["assistant"]))
        return "Conversation history saved"
    except Exception as e:
        return f"Failed to save conversation: {e}"


def load_conversation(file_path: str | Path | None = None) -> list[dict]:
    if file_path is not None:
        target = _path(file_path)
        if not target.exists():
            return []
        try:
            history = json.loads(target.read_text(encoding="utf-8"))
            if not isinstance(history, list):
                return []
            return [
                item for item in history
                if isinstance(item, dict)
                and isinstance(item.get("user"), str)
                and isinstance(item.get("assistant"), str)
            ][-MAX_CONVERSATION_TURNS:]
        except Exception as e:
            print(f"[Memory] Conversation history load failed: {e}")
            return []
    from memory import db
    return db.load_conversation_turns()


def append_conversation(user: str, assistant: str, file_path: str | Path | None = None,
                        conversation_id: int | None = None) -> list[dict]:
    if file_path is not None:
        history = load_conversation(file_path)
        history.append({"timestamp": datetime.now().isoformat(timespec="seconds"), "user": user.strip(), "assistant": assistant.strip()})
        result = save_conversation(history, file_path)
        if result.startswith("Failed"):
            raise OSError(result)
        return history
    from memory import db
    db.append_conversation_turn(user, assistant, conversation_id=conversation_id)
    return db.load_conversation_turns(conversation_id=conversation_id)


def load_user_memory(file_path: str | Path | None = None) -> dict:
    """Load persistent user profile details and context across conversations."""
    target = _user_memory_path(file_path)
    default_memory = {
        "profile": {},
        "preferences": {},
        "facts": [],
        "context": {},
        "updated_at": None,
    }
    if not target.exists():
        return default_memory
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            for key, val in default_memory.items():
                data.setdefault(key, val)
            return data
        return default_memory
    except Exception as e:
        print(f"[Memory] User memory load failed: {e}")
        return default_memory


def save_user_memory(memory: dict, file_path: str | Path | None = None) -> str:
    """Persist user profile, facts, and past context to disk."""
    target = _user_memory_path(file_path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = memory if isinstance(memory, dict) else {}
        payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
        text = json.dumps(payload, indent=2, ensure_ascii=False)
        tmp = target.with_suffix(target.suffix + ".tmp")
        if target.exists():
            try:
                import shutil
                shutil.copy2(target, target.with_suffix(target.suffix + ".bak"))
            except Exception:
                pass
        tmp.write_text(text, encoding="utf-8")
        import os
        os.replace(tmp, target)
        return f"User memory saved to {target}"
    except Exception as e:
        return f"Failed to save user memory: {e}"


def update_user_memory(
    profile: dict | None = None,
    preferences: dict | None = None,
    facts: list[str] | str | None = None,
    context: dict | None = None,
    file_path: str | Path | None = None,
) -> dict:
    """Update and persist user details, preferences, facts, or context."""
    memory = load_user_memory(file_path)

    if profile and isinstance(profile, dict):
        memory.setdefault("profile", {}).update(profile)

    if preferences and isinstance(preferences, dict):
        memory.setdefault("preferences", {}).update(preferences)

    if facts:
        existing_facts = set(memory.setdefault("facts", []))
        new_facts = [facts] if isinstance(facts, str) else list(facts)
        for fact in new_facts:
            clean_fact = str(fact).strip()
            if clean_fact and clean_fact not in existing_facts:
                memory["facts"].append(clean_fact)
                existing_facts.add(clean_fact)

    if context and isinstance(context, dict):
        memory.setdefault("context", {}).update(context)

    save_result = save_user_memory(memory, file_path)
    if save_result.startswith("Failed"):
        raise OSError(save_result)
    return memory


def get_persistent_context(file_path: str | Path | None = None) -> str:
    """Format persistent user details and past context as text for LLM prompts."""
    memory = load_user_memory(file_path)
    sections = []

    profile = memory.get("profile", {})
    if profile:
        profile_lines = [f"- {k}: {v}" for k, v in profile.items() if v]
        if profile_lines:
            sections.append("User Profile:\n" + "\n".join(profile_lines))

    preferences = memory.get("preferences", {})
    if preferences:
        pref_lines = [f"- {k}: {v}" for k, v in preferences.items() if v]
        if pref_lines:
            sections.append("Preferences:\n" + "\n".join(pref_lines))

    facts = memory.get("facts", [])
    if facts:
        fact_lines = [f"- {fact}" for fact in facts if fact]
        if fact_lines:
            sections.append("Known Facts & Background:\n" + "\n".join(fact_lines))

    context = memory.get("context", {})
    if context:
        context_lines = [f"- {k}: {v}" for k, v in context.items() if v]
        if context_lines:
            sections.append("Past Context & Ongoing Topics:\n" + "\n".join(context_lines))

    return "\n\n".join(sections)


if __name__ == "__main__":
    history = [{"user": "Hello", "assistant": "Hi there!"}]
    print(save_conversation(history))
    print(load_conversation())
    update_user_memory(
        profile={"name": "Alex", "role": "Developer"},
        preferences={"tone": "concise"},
        facts=["Working on an AI assistant project."],
    )
    print("\nPersistent Context:")
    print(get_persistent_context())

def recent_messages(channel=None, budget=4800, conversation_id=None) -> list[dict]:
    """Read a bounded chronological tail as data with explicit speaker roles."""
    from memory import db
    messages = []
    for turn in reversed(db.load_conversation_turns(12, channel=channel, conversation_id=conversation_id, include_pending=False)):
        user = turn["user"][:800]
        reply = turn["assistant"][:1000]
        size = len(user) + len(reply)
        if size > budget:
            break
        messages.append([{"role": "user", "content": user}, {"role": "assistant", "content": reply}])
        budget -= size
    return [message for pair in reversed(messages) for message in pair]


def build_turn_context(query, *, conversation_id=None, share_cloud=False):
    """Return bounded, conversation-scoped context only with explicit sharing enabled."""
    if not share_cloud:
        return [], ""
    from memory import db
    from memory.memory_manager import load_memory, format_memory_for_prompt
    from core.cloud_privacy import redact_text
    if conversation_id is None:
        active = db.get_active_conversation()
        conversation_id = active["id"] if active else None
    if conversation_id is None:
        return [], ""
    history = recent_messages(conversation_id=conversation_id)
    seen = {(m["role"], m["content"]) for m in history}
    snippets = []
    for turn in db.search_conversation_turns(
        query, conversation_id=conversation_id, limit=4, include_pending=False,
    ):
        if ("user", turn["user"][:800]) not in seen:
            snippets.append({"user": turn["user"][:400], "assistant": turn["assistant"][:500]})
    facts = format_memory_for_prompt(load_memory())[:1500]
    context = json.dumps({"stored_facts": facts, "relevant_earlier_turns": snippets}, ensure_ascii=False)
    history = [{**msg, "content": redact_text(msg["content"])} for msg in history]
    return history, redact_text(context[:4000])


def recall_local(query: str, *, conversation_id=None):
    """Explicit conversation-history requests are answered locally, never via an LLM."""
    import re
    from memory import db
    query = query.strip()
    normalized = query.casefold().rstrip("?.!").strip()
    recent = {"what did i say last", "what did i say", "what was my last message",
              "what did we talk about", "what did we discuss last",
              "show previous conversation", "show last conversation",
              "maine last kya kaha", "maine kya kaha tha", "pichli baat batao",
              "pichli conversation batao", "last conversation batao"}
    match = re.fullmatch(r"(?:search (?:my )?(?:conversation|chat) history for|recall conversation about|history search|purani chat mein dhundo)\s*[: ]\s*(.+)", query, re.I)
    if match is None:
        match = re.fullmatch(r"(?:what did (?:i|we) (?:say|discuss|talk) about|do you remember what (?:i|we) (?:said|discussed) about|what was our discussion about|(?:maine|humne) (?:kya kaha|kya discuss kiya) tha(?: about)?|yaad hai)\s+(.+?)[?.!]*", query, re.I)
    if normalized not in recent and match is None:
        return None
    if conversation_id is None:
        active = db.get_active_conversation()
        conversation_id = active["id"] if active else None
    if conversation_id is None:
        return "No saved conversation is available."
    if match:
        rows = db.search_conversation_turns(match.group(1), conversation_id=conversation_id, limit=3)
    else:
        rows = list(reversed(db.load_conversation_turns(3, conversation_id=conversation_id)))
    # Do not present previous recall output recursively.
    rows = [r for r in rows if not r["assistant"].startswith("[LOCAL MEMORY]")]
    if not rows:
        return "[LOCAL MEMORY] No matching saved conversation turns."
    return "[LOCAL MEMORY] Saved conversation:\n" + "\n\n".join(
        "You: " + r["user"][:400] + "\nMARK: " + r["assistant"][:600] for r in rows)
