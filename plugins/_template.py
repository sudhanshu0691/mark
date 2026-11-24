"""
PLUGIN TEMPLATE  -  copy this file, rename it (no leading underscore) and edit.
Mark loads it automatically within ~2 seconds; no other file needs to change.
"""
from core.result import done, failed        # honest results: done / unverified / failed

NAME = "my_tool"
DESCRIPTION = "One line about what this plugin does."
METADATA = {
    "version": "1.0.0",
    "capabilities": ["replace_with", "searchable", "capabilities"],
    "permissions": [],  # Tool names this plugin may call through ctx.call_tool; "*" allows all.
    "timeout": 30,
}

DECLARATION = {
    "name": "my_tool",                                   # unique tool name
    "description": "Tell Gemini WHEN to use this tool.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "text": {"type": "STRING", "description": "Some input"},
        },
        "required": ["text"],
    },
}


def run(args: dict, ctx):
    """args = what Gemini sent.  ctx = PluginContext (ctx.speak, ctx.log, ctx.call_tool, ctx.base_dir)."""
    text = args.get("text", "")
    if not text:
        return failed("my_tool did nothing", "no text was given")
    # ... do the real work here, then CHECK it worked before returning done(...)
    return done(f"my_tool handled: {text}")


# Optional hooks ---------------------------------------------------------------
# def on_load(ctx): ...                      # when plugin is (re)loaded
# def health_check(ctx): return True         # optional readiness check
# def on_notification(note, ctx): ...        # every new Windows notification
#                                            # note = {"app": "WhatsApp", "texts": [...], "is_call": False}
# def on_unload(ctx): ...
