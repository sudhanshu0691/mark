"""Workspaces: save a set of apps/sites as a mode ("study mode", "work mode") and open them all with one command."""
import json
from pathlib import Path

from core.result import done, failed, unverified

NAME = "workspace_modes"
DESCRIPTION = "Save and launch named sets of apps/websites (work mode, study mode, ...)."
METADATA = {"version": "1.0.0", "capabilities": ["workspace", "mode", "launch", "routine"], "permissions": ["open_app"], "timeout": 120}
_FILE = Path(__file__).resolve().parent.parent / "memory" / "workspaces.json"

DECLARATION = {
    "name": "workspace",
    "description": "Named app bundles. action=save (name + items like ['VS Code','Chrome|https://github.com','Spotify']), run (name), list, delete (name). "
                   "Use for 'work mode on karo', 'study mode save karo'.",
    "parameters": {"type": "OBJECT", "properties": {
        "action": {"type": "STRING", "description": "save | run | list | delete"}, "name": {"type": "STRING", "description": "Mode name"},
        "items": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Apps; use 'App|url' to open a site in a browser"}}, "required": ["action"]},
}


def _load():
    try:
        return json.loads(_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def run(args, ctx):
    act = str(args.get("action", "list")).lower()
    name = str(args.get("name", "")).strip().lower()
    data = _load()
    if act == "list":
        return done(f"{len(data)} workspace(s)", evidence="\n".join(f"{k}: {', '.join(v)}" for k, v in data.items())) if data else done("No workspaces saved yet")
    if act == "save":
        items = [str(i).strip() for i in (args.get("items") or []) if str(i).strip()]
        if not name or not items:
            return failed("Workspace not saved", "need a name and at least one app")
        data[name] = items
        _FILE.parent.mkdir(exist_ok=True)
        _FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        return done(f"Workspace '{name}' saved with {len(items)} item(s)", evidence=", ".join(items)) if _load().get(name) == items else failed("Not saved", "write failed")
    if act == "delete":
        if name not in data:
            return failed("Nothing deleted", f"no workspace '{name}'")
        data.pop(name)
        _FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        return done(f"Workspace '{name}' deleted")
    if act == "run":
        if name not in data:
            return failed(f"No workspace '{name}'", "saved: " + (", ".join(data) or "none"))
        ok, bad = [], []
        for it in data[name]:
            app, _, url = it.partition("|")
            res = ctx.call_tool("open_app", {"app_name": app.strip(), **({"url": url.strip()} if url.strip() else {}), "maximize": False})
            (ok if res.startswith("[DONE]") else bad).append(app.strip() + ("" if res.startswith("[DONE]") else f" ({res.splitlines()[0][:60]})"))
        if not bad:
            return done(f"Workspace '{name}' is up: {', '.join(ok)}", evidence="each app verified in front when opened")
        return unverified(f"Workspace '{name}': {len(ok)} ok, {len(bad)} not confirmed", reason="; ".join(bad), evidence="ok: " + ", ".join(ok))
    return failed("Unknown workspace action", "valid: save run list delete")
