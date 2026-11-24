"""Example working plugin: read / write the Windows clipboard."""
from core.result import done, failed

NAME = "clipboard_tools"
DESCRIPTION = "Read or set the clipboard text."

DECLARATION = {
    "name": "clipboard",
    "description": "Read the current clipboard text, or put text on the clipboard.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "get | set"},
            "text": {"type": "STRING", "description": "Text to copy (for set)"},
        },
        "required": ["action"],
    },
}


def run(args, ctx):
    try:
        import pyperclip
    except ImportError:
        return failed("Clipboard not used", "pyperclip is not installed (pip install pyperclip)")
    action = str(args.get("action", "get")).lower()
    if action == "get":
        return done("Clipboard contains:\n" + (pyperclip.paste() or "(empty)"))
    text = str(args.get("text", ""))
    pyperclip.copy(text)
    return done("Copied to clipboard.", evidence="read-back matches") if pyperclip.paste() == text \
        else failed("Clipboard not set", "read-back did not match")
