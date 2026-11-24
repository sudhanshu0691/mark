"""Risk classification for actions. Used to gate destructive/sensitive operations."""
from __future__ import annotations

READ_ONLY = "read"
WRITE = "write"
DESTRUCTIVE = "destructive"
EXTERNAL = "external"

RISK_MAP = {
    "file_controller": {
        "drives": READ_ONLY, "list": READ_ONLY, "read": READ_ONLY, "info": READ_ONLY,
        "find": READ_ONLY, "disk_usage": READ_ONLY,
        "write": WRITE, "append": WRITE, "mkdir": WRITE, "copy": WRITE,
        "move": WRITE, "rename": WRITE, "zip": WRITE, "unzip": WRITE, "open": WRITE,
        "delete": DESTRUCTIVE,
    },
    "whatsapp": {"send": EXTERNAL, "read": READ_ONLY, "open": WRITE},
    "send_message": {"*": EXTERNAL},
    "calls": {"*": EXTERNAL},
    "software_manager": {"*": WRITE},
    "open_app": {"*": WRITE},
    "browser_control": {"*": WRITE},
    "computer_control": {"*": WRITE},
    "computer_settings": {"*": WRITE},
    "desktop_control": {"*": WRITE},
    "window_manager": {"*": WRITE},
    "code_helper": {"*": WRITE},
    "dev_agent": {"*": WRITE},
    "file_processor": {"*": WRITE},
}

# Discrete operations required for the deterministic local route.
READ_ONLY_PLUGINS = {"runtime_status", "calculate", "news_plugin", "weather_plugin", "vision_status", "vision_history"}
SAFE_SETTINGS = {"volume_up", "volume_down", "volume_mute", "volume_set", "brightness_set", "copy", "select_all", "undo", "redo"}
RISK_MAP.update({
    "computer_settings": {**dict.fromkeys(SAFE_SETTINGS, WRITE), "*": DESTRUCTIVE},
    "computer_control": {"screenshot": READ_ONLY, "*": EXTERNAL},
    "hardware_control": {"mute": WRITE, "volume": WRITE, "battery": READ_ONLY, "info": READ_ONLY, "*": DESTRUCTIVE},
    "browser_control": {**dict.fromkeys(("open", "navigate", "tabs", "list_tabs", "read", "back", "forward"), WRITE), "*": EXTERNAL},
    "desktop_control": {"clean": DESTRUCTIVE, "organize": DESTRUCTIVE, "*": WRITE},
    "processes": {"top": READ_ONLY, "find": READ_ONLY, "*": DESTRUCTIVE}, "app_integration": {"list": READ_ONLY, "status": READ_ONLY, "focus": WRITE, "launch": WRITE, "*": DESTRUCTIVE},
    "whatsapp": {"read": READ_ONLY, "open": WRITE, "*": EXTERNAL},
    "send_email": {"*": EXTERNAL}, "message_delivery": {"*": EXTERNAL},
    "plugins": {"remove": DESTRUCTIVE, "reload": WRITE}, "vision_start": {"*": EXTERNAL},
    "call_agent": {"status": READ_ONLY, "*": EXTERNAL},
    "software_manager": {"list": READ_ONLY, "search": READ_ONLY, "*": DESTRUCTIVE},
    "excel": {"inspect": READ_ONLY, "profile": READ_ONLY, "*": DESTRUCTIVE},
    "code_helper": {"explain": READ_ONLY, "*": DESTRUCTIVE}, "dev_agent": {"*": DESTRUCTIVE},
    "file_controller": {**RISK_MAP["file_controller"], **dict.fromkeys(("move", "write", "rename", "unzip", "copy", "zip", "open"), DESTRUCTIVE)},
    "window_manager": {"close": DESTRUCTIVE, "*": WRITE},
    "shutdown_mark": {"*": DESTRUCTIVE},
    "screen_process": {"*": EXTERNAL},
    "screen_read": {"ask": EXTERNAL},
})

# Explicit interaction categories override the conservative defaults above.
LOW, MEDIUM, HIGH = READ_ONLY, WRITE, DESTRUCTIVE
RISK_MAP["computer_settings"]["*"] = MEDIUM
RISK_MAP["computer_settings"].update(dict.fromkeys(
    ("shutdown", "restart", "sleep", "lock", "kill_app", "format", "security"), HIGH))
RISK_MAP["file_controller"]["open"] = LOW
RISK_MAP["open_app"] = {"*": LOW}
RISK_MAP["gesture_mouse"] = {"*": LOW}
RISK_MAP["computer_control"].update(dict.fromkeys(
    ("scroll", "move", "click", "double_click", "right_click", "screen_click",
     "screen_find", "screenshot", "focus_window", "wait", "copy"), LOW))
RISK_MAP["computer_settings"].update(dict.fromkeys(
    ("zoom_in", "zoom_out", "zoom_reset", "scroll_up", "scroll_down", "scroll_top",
     "scroll_bottom", "page_up", "page_down", "go_back", "go_forward"), LOW))
RISK_MAP["browser_control"].update(dict.fromkeys(
    ("open", "navigate", "tabs", "list_tabs", "read", "back", "forward", "scroll"), LOW))
RISK_MAP["window_manager"].update(dict.fromkeys(
    ("list", "active", "focus", "switch", "maximize", "minimize", "snap_left", "snap_right", "wait"), LOW))
RISK_MAP["software_manager"].update({"install": MEDIUM, "uninstall": HIGH})
RISK_MAP["file_controller"].update(dict.fromkeys(
    ("write", "append", "mkdir", "copy", "rename", "zip", "unzip"), MEDIUM))
RISK_MAP["file_controller"]["move"] = HIGH
RISK_MAP["computer_control"].update({"left_click": LOW, "drag": MEDIUM})
RISK_MAP["browser_control"].update(dict.fromkeys(
    ("go_to", "search", "new_tab", "switch", "list_browsers", "click", "smart_click",
     "get_text", "get_url", "reload", "screenshot"), LOW))
RISK_MAP["excel"].update(dict.fromkeys(
    ("create", "append", "set_cell", "formula", "format", "sort", "summary", "chart", "recalculate"), MEDIUM))
RISK_MAP["excel"]["remove_duplicates"] = HIGH
RISK_MAP["computer_settings"].update(dict.fromkeys(
    ("empty_recycle_bin", "empty_trash", "log_off", "logout", "sign_out", "hibernate",
     "hibernate_computer", "sleep_computer", "restart_computer", "shutdown_computer",
     "close_process", "force_close", "kill_process"), HIGH))

# Only well-known navigation shortcuts are safe to infer from generic keyboard tools.
_NAV_KEYS = {"up", "down", "left", "right", "pageup", "pagedown", "home", "end", "esc", "escape", "tab"}
_NAV_HOTKEYS = {("ctrl", "equal"), ("ctrl", "+"), ("ctrl", "plus"), ("ctrl", "-"),
                ("ctrl", "minus"), ("ctrl", "0"), ("alt", "left"), ("alt", "right"),
                ("ctrl", "home"), ("ctrl", "end")}


def classify(tool: str, args: dict | None = None) -> str:
    args = args or {}
    cfg = RISK_MAP.get(tool, {})
    action = str(args.get("action") or args.get("mode") or "*").strip().lower().replace("-", "_").replace(" ", "_")
    if tool == "computer_control" and action == "press":
        return LOW if str(args.get("key", "")).strip().lower() in _NAV_KEYS else HIGH
    if tool == "computer_control" and action == "hotkey":
        raw = args.get("keys", "")
        keys = raw.split("+") if isinstance(raw, str) else raw if isinstance(raw, (tuple, list)) else []
        # Preserve the common Ctrl++ spelling, whose final key is itself '+'.
        if isinstance(raw, str) and raw.strip().lower() in {"ctrl++", "control++"}:
            keys = ["ctrl", "+"]
        keys = tuple("ctrl" if str(k).strip().lower() == "control" else str(k).strip().lower() for k in keys)
        return LOW if keys in _NAV_HOTKEYS else HIGH
    if action in cfg:
        return cfg[action]
    if "*" in cfg:
        return cfg["*"]
    return READ_ONLY

def requires_confirmation(tool: str, args: dict | None = None) -> bool:
    return classify(tool, args) in (DESTRUCTIVE, EXTERNAL)


def validate_arguments(declaration, args):
    if declaration is None:
        return "Unknown tool."
    if not isinstance(args, dict):
        return "Arguments must be an object."
    types = {"STRING": str, "BOOLEAN": bool, "INTEGER": int, "NUMBER": (int, float),
             "ARRAY": list, "OBJECT": dict}

    def validate(schema, value, path, depth=0, strict=False):
        if depth > 32 or not isinstance(schema, dict):
            return f"Invalid schema for {path}."
        kind = str(schema.get("type", "")).upper()
        if kind in types and (not isinstance(value, types[kind]) or
                (kind in {"NUMBER", "INTEGER"} and isinstance(value, bool))):
            return f"Invalid type for {path}."
        if "enum" in schema and value not in schema["enum"]:
            return f"Invalid value for {path}."
        if isinstance(value, dict):
            fields = schema.get("properties") or {}
            for key in schema.get("required", []):
                if key not in value:
                    missing = f"{path}.{key}" if path else key
                    return f"Missing required argument {missing}."
            for key, item in value.items():
                child = f"{path}.{key}" if path else str(key)
                if key not in fields:
                    if strict or schema.get("additionalProperties") is False:
                        return f"Unknown argument {child}."
                    continue
                error = validate(fields[key], item, child, depth + 1)
                if error:
                    return error
        elif isinstance(value, list) and isinstance(schema.get("items"), dict):
            for index, item in enumerate(value):
                error = validate(schema["items"], item, f"{path}[{index}]", depth + 1)
                if error:
                    return error
        return ""

    return validate(declaration.get("parameters") or {}, args, "", strict=True)
