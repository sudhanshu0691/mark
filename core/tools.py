"""
Declarations + routing for the new OS-control tools.
main.py asks this module first; unknown names fall through to the legacy tools
and to plugins.
"""
from __future__ import annotations

from actions.calls import calls
from actions.fs_full import fs_control
from actions.notifications import notifications
from actions.software import software_manager
from actions.whatsapp import whatsapp
from actions.win_manager import window_manager
from actions.screen_read import screen_read
from actions.visualize import visualize
from actions.call_agent import call_agent

NEW_DECLARATIONS: list[dict] = [
    {
        "name": "file_controller",
        "description": (
            "FULL file-system control on every drive: list, read, write, append, mkdir, copy, move, rename, delete "
            "(Recycle Bin by default), find (by name/extension/text inside), info, zip, unzip, open, drives, disk_usage. "
            "Path shortcuts: desktop, downloads, documents, pictures, music, videos, home, temp, or any full path like D:\\Work\\a.txt."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "drives | list | read | write | append | mkdir | copy | move | rename | delete | find | info | zip | unzip | open | disk_usage"},
                "path": {"type": "STRING", "description": "File or folder path or shortcut"},
                "name": {"type": "STRING", "description": "File name (joined to path), or the name to search for with find"},
                "destination": {"type": "STRING", "description": "Target path for copy/move/zip/unzip"},
                "new_name": {"type": "STRING", "description": "New name for rename"},
                "content": {"type": "STRING", "description": "Text to write/append"},
                "extension": {"type": "STRING", "description": "Extension filter for find, e.g. pdf"},
                "contains": {"type": "STRING", "description": "For find: only files whose text contains this"},
                "permanent": {"type": "BOOLEAN", "description": "delete: skip Recycle Bin (irreversible)"},
                "confirmed": {"type": "BOOLEAN", "description": "Set true ONLY after the user explicitly confirmed a permanent delete"},
                "append": {"type": "BOOLEAN", "description": "write: append instead of overwrite"},
                "show_hidden": {"type": "BOOLEAN", "description": "list: include dotfiles"},
                "max_results": {"type": "INTEGER", "description": "find: result limit"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "whatsapp",
        "description": (
            "WhatsApp Desktop on this PC. action=send (contact + message; verified), read (visible chat text), open (open a chat). "
            "Use this for ALL WhatsApp messages. Reports honestly if the message could not be sent."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "send | read | open"},
                "contact": {"type": "STRING", "description": "Contact name or phone number with country code"},
                "message": {"type": "STRING", "description": "Text to send"},
                "count": {"type": "INTEGER", "description": "How many lines to read"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "calls",
        "description": (
            "Control calls on the PC: answer / decline an incoming call (WhatsApp, Phone Link, Teams...), hangup, "
            "call a WhatsApp contact (voice or video), or dial a normal number through Phone Link."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "answer | decline | hangup | call | video | dial"},
                "contact": {"type": "STRING", "description": "Contact name for call/video"},
                "number": {"type": "STRING", "description": "Phone number for dial"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "notifications",
        "description": (
            "Read Windows notifications (WhatsApp, Telegram, mail, calls...). action=recent (optionally app=WhatsApp), "
            "announce_on / announce_off (speak new notifications automatically), status, clear."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "recent | announce_on | announce_off | status | clear | start"},
                "app": {"type": "STRING", "description": "Filter by app name"},
                "count": {"type": "INTEGER", "description": "How many to return"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "software_manager",
        "description": (
            "Install / uninstall / search software on Windows (winget, Chocolatey, Scoop) or Python/npm packages. "
            "Searches first, installs the best match, then verifies it is really installed."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "install | uninstall | search | list"},
                "name": {"type": "STRING", "description": "App name, e.g. 'VLC', 'Visual Studio Code'"},
                "package_id": {"type": "STRING", "description": "Exact winget id if known, e.g. VideoLAN.VLC"},
                "source": {"type": "STRING", "description": "auto (default) | pip | npm"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "window_manager",
        "description": (
            "Know and control what is open on the PC (the taskbar). action=list (all open windows + taskbar pins), active (front window), "
            "focus/switch (bring an app to front, maximized; if it is not open it is started), maximize, minimize, restore, close, "
            "snap_left/snap_right, wait (until a window title appears). Results are VERIFIED against the real front window. "
            "Use list/active BEFORE opening something to see if it is already open."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "list | active | focus | switch | maximize | minimize | restore | close | snap_left | snap_right | wait"},
                "app": {"type": "STRING", "description": "App or window name, e.g. 'Chrome', 'WhatsApp', 'Spotify'. Omit to act on the front window."},
                "title": {"type": "STRING", "description": "Window title text/regex (for wait)"},
                "timeout": {"type": "NUMBER", "description": "Seconds to wait (wait action, default 15)"},
                "maximize": {"type": "BOOLEAN", "description": "focus: maximize the window (default true)"},
                "url": {"type": "STRING", "description": "focus on a browser: also open this URL in a new tab"},
                "new_tab": {"type": "BOOLEAN", "description": "focus on a browser: open a fresh tab"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "visualize",
        "description": (
            "Analyse a project folder and DRAW it, or make any diagram / drawing. Saves SVG+PNG+PDF (DXF for drawings) on an "
            "ISO-style sheet with title block and legend, and shows the picture in the middle of the screen. "
            "action=analyze (path) summarises a project; action=diagram (path, diagram_type auto|component|package|module|class|erd) "
            "draws UML 2.5 / C4 / ERD diagrams from the real code; action=flowchart (spec, or path+function for a Python function) draws an "
            "ISO 5807 flowchart; action=sequence (spec) draws a UML sequence diagram; action=drawing (spec) makes a 2D engineering drawing. "
            "spec is JSON TEXT. flowchart spec: {title, steps:[{id,type(start|process|decision|io|document|predefined|end),text}], edges:[{from,to,label}]}. "
            "sequence spec: {title, participants:[..], messages:[{from,to,text,kind(call|return)}]}. "
            "drawing spec: {title, sheet, scale, entities:[{type:line|rect|circle|arc|polyline|text|dim, ...}]} in millimetres. "
            "Use this whenever the user asks for a diagram, chart of a project, flowchart, architecture, ERD, UML, visualisation or a drawing."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "analyze | diagram | flowchart | sequence | drawing"},
                "path": {"type": "STRING", "description": "Project folder or file path (analyze, diagram, flowchart from function)"},
                "diagram_type": {"type": "STRING", "description": "auto | component | package | module | class | erd (comma separated allowed)"},
                "function": {"type": "STRING", "description": "flowchart: name of the Python function in `path`"},
                "spec": {"type": "STRING", "description": "JSON text describing the flowchart / sequence / drawing"},
                "title": {"type": "STRING", "description": "Optional title / file name"},
                "owner": {"type": "STRING", "description": "Optional owner / project name for the title block"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "call_agent",
        "description": (
            "Answer phone/WhatsApp calls FOR the user while they are away - only the callers the user names, only when the user asks. "
            "action=arm (callers: names or phone numbers, minutes, purpose, confirmed=true ONLY if the user explicitly asked in this conversation) "
            "turns it on; action=disarm turns it off; action=status; action=stop ends a running call at once; action=recent lists recorded calls "
            "with summaries. Mark always says it is an AI assistant and that the call is recorded. Never arm it on your own."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "arm | disarm | status | stop | recent"},
                "callers": {"type": "STRING", "description": "Comma separated names and/or phone numbers to answer for"},
                "minutes": {"type": "NUMBER", "description": "How long the rule stays on (default 120, max 720)"},
                "purpose": {"type": "STRING", "description": "What Mark should do on the call, e.g. 'take a message, say I will call back tonight'"},
                "confirmed": {"type": "BOOLEAN", "description": "true only after the user explicitly asked to answer calls for them"},
                "count": {"type": "INTEGER", "description": "recent: how many calls"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "screen_read",
        "description": (
            "RELIABLE way to see the screen. Prefer this over screen_process. mode=state (exact list of open windows + which is in front; no AI), "
            "text (real on-screen text via UI-Automation/OCR), ask (screenshot answered by Gemini vision: put the question in `question`), "
            "verify (PASS/FAIL: expect_window regex and/or expect_text must really be on screen; optional wait seconds). "
            "ALWAYS use verify/state to check that an app, chat, file or button you opened/selected is the right one before saying it is done."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "mode": {"type": "STRING", "description": "state | text | ask | verify (default state)"},
                "question": {"type": "STRING", "description": "ask: what to find out about the screen"},
                "expect_window": {"type": "STRING", "description": "verify: regex for the front window title or exe, e.g. 'chrome|youtube'"},
                "expect_text": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "verify: text that must be visible, e.g. ['Rahul']"},
                "wait": {"type": "NUMBER", "description": "verify: keep re-checking for up to this many seconds"},
                "monitor": {"type": "INTEGER", "description": "Monitor index (1 = primary)"},
            },
            "required": [],
        },
    },
    {
        "name": "run_goal",
        "description": (
            "Autonomous brain for multi-step tasks (3+ steps, or when the way to reach the goal is unclear): it plans, "
            "uses the other tools, checks each result, retries differently when something fails and returns an honest report. "
            "Example: 'install VLC then open it', 'find my resume and WhatsApp it to Rahul'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "goal": {"type": "STRING", "description": "The full goal in plain words"},
                "max_steps": {"type": "INTEGER", "description": "Step limit (default 10)"},
                "visual_checkpoints": {"type": "BOOLEAN", "description": "Capture a screen checkpoint after each successful action"},
            },
            "required": ["goal"],
        },
    },
    {
        "name": "plugins",
        "description": (
            "Manage the plugins/ folder: list loaded plugins and errors, discover capabilities, reload reviewed extensions "
            "or disable one."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "list | discover | reload | remove"},
                "name": {"type": "STRING", "description": "Plugin file name without .py"},
                "query": {"type": "STRING", "description": "Capability or tool text to search for"},
            },
            "required": ["action"],
        },
    },
]

# plain functions: (args, ui) -> str
NATIVE = {
    "file_controller": lambda args, ui: fs_control(args, player=ui),
    "whatsapp": lambda args, ui: whatsapp(args, player=ui),
    "calls": lambda args, ui: calls(args, player=ui),
    "notifications": lambda args, ui: notifications(args, player=ui),
    "software_manager": lambda args, ui: software_manager(args, player=ui),
    "window_manager": lambda args, ui: str(window_manager(args, player=ui)),
    "screen_read": lambda args, ui: str(screen_read(args, player=ui)),
    "visualize": lambda args, ui: visualize(args, player=ui),
    "call_agent": lambda args, ui: call_agent(args, player=ui),
}
NEW_NAMES = {d["name"] for d in NEW_DECLARATIONS}
