"""MARK Root Agent tools for camera monitoring and vision event queries."""
from __future__ import annotations

import json

NAME = "vision"
DESCRIPTION = "MARK Vision Department: control the configured camera monitor and query human-detection history."
METADATA = {
    "version": "1.0.0",
    "capabilities": ["vision", "camera monitoring", "human detection", "detection history"],
    "permissions": [],
    "timeout": 30,
}


def _response(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _department():
    from vision_department.runtime import get_department
    return get_department()


def _status(args, ctx):
    return "[DONE] MARK Vision status\n" + _response(_department().status)


def _start(args, ctx):
    if args.get("confirmed") is not True:
        return "[FAILED] Camera monitoring requires confirmed=true after the user explicitly asks to start it."
    try:
        department = _department()
        department.start()
        return "[DONE] MARK Vision monitoring started\n" + _response(department.status)
    except Exception as exc:
        return f"[FAILED] MARK Vision could not start\nREASON: {type(exc).__name__}: {exc}"


def _stop(args, ctx):
    department = _department()
    department.stop()
    return "[DONE] MARK Vision monitoring stopped\n" + _response(department.status)


def _history(args, ctx):
    limit = max(1, min(int(args.get("limit", 50)), 500))
    return "[DONE] MARK Vision event history\n" + _response(_department().history(limit))


TOOLS = [
    {"declaration": {"name": "vision_status", "description": "Get MARK Vision camera, model, agent health, and event count.",
                     "parameters": {"type": "OBJECT", "properties": {}}}, "run": _status},
    {"declaration": {"name": "vision_start", "description": "Start local human detection from the configured Qubo camera. Only call after the user explicitly requests monitoring and set confirmed=true.",
                     "parameters": {"type": "OBJECT", "properties": {"confirmed": {"type": "BOOLEAN", "description": "Must be true only after explicit user authorization."}}, "required": ["confirmed"]}}, "run": _start},
    {"declaration": {"name": "vision_stop", "description": "Stop MARK Vision camera monitoring.",
                     "parameters": {"type": "OBJECT", "properties": {}}}, "run": _stop},
    {"declaration": {"name": "vision_history", "description": "Query recent MARK Vision human and motion event metadata.",
                     "parameters": {"type": "OBJECT", "properties": {"limit": {"type": "INTEGER", "description": "Maximum events to return (1–500)."}}}}, "run": _history},
]


def on_load(ctx):
    # Root alert delivery uses MARK's existing native notification panel.
    if ctx.ui is None:
        return
    def alert(event):
        note = {"source": "MARK Vision", **event}
        try:
            ctx.ui.add_notification(note)
        except Exception:
            pass
        dashboard = getattr(ctx.ui, "vision_dashboard", None)
        if dashboard is not None:
            dashboard.alert_ready.emit(event)
    _department().set_callbacks(on_event=alert)
