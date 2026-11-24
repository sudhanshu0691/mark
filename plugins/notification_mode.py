"""Control how Mark relays Windows notifications."""
from __future__ import annotations

import json
from pathlib import Path
import threading

from core.result import done, failed

NAME = "notification_mode"
DESCRIPTION = (
    "Controls notification relay: normal speaks and shows messages, silent only "
    "shows them in Mark, and dnd hides them completely."
)
METADATA = {
    "version": "1.0.0",
    "capabilities": ["notifications", "silent mode", "do not disturb"],
    "permissions": [],
    "timeout": 10,
}

DECLARATION = {
    "name": "notification_mode",
    "description": (
        "Set or check Mark notification mode. normal speaks and shows notifications; "
        "silent does not read them aloud but keeps them visible in the conversation; "
        "dnd hides notifications from Mark completely."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "Use 'set' or 'status'.",
                "enum": ["set", "status"],
            },
            "mode": {
                "type": "STRING",
                "description": "Notification mode when action is set.",
                "enum": ["normal", "silent", "dnd"],
            },
        },
        "required": ["action"],
    },
}

_VALID_MODES = {"normal", "silent", "dnd"}
_lock = threading.RLock()
_mode = "normal"
_path: Path | None = None


def _config_path(ctx) -> Path:
    return Path(ctx.base_dir) / "memory" / "notification_mode.json"


def on_load(ctx) -> None:
    global _mode, _path
    with _lock:
        _path = _config_path(ctx)
        try:
            value = json.loads(_path.read_text(encoding="utf-8")).get("mode", "normal")
        except FileNotFoundError:
            value = "normal"
        except (OSError, ValueError, AttributeError) as exc:
            ctx.log(f"notification mode config could not be read: {exc}")
            value = "normal"
        _mode = value if value in _VALID_MODES else "normal"


def _save() -> None:
    if _path is None:
        raise RuntimeError("notification mode plugin is not loaded")
    _path.parent.mkdir(parents=True, exist_ok=True)
    _path.write_text(json.dumps({"mode": _mode}, indent=2) + "\n", encoding="utf-8")


def run(args: dict, ctx):
    global _mode
    action = str(args.get("action", "status")).lower().strip()
    if action == "status":
        with _lock:
            return done(f"Notification mode is '{_mode}'.")
    if action != "set":
        return failed("Notification mode action rejected", "valid actions: set, status")
    mode = str(args.get("mode", "")).lower().strip()
    if mode not in _VALID_MODES:
        return failed("Notification mode rejected", "mode must be normal, silent, or dnd")
    with _lock:
        _mode = mode
        try:
            _save()
        except OSError as exc:
            return failed("Notification mode was not saved", f"{type(exc).__name__}: {exc}")
    return done(f"Notification mode set to '{mode}'.")


def notification_policy(note: dict, ctx) -> str:
    with _lock:
        return _mode
