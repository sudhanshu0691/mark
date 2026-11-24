"""Voice notes: add / list / search / delete short notes (stored in memory/notes.json)."""
import json
import time
from pathlib import Path

from core.result import done, failed

NAME = "quick_notes"
DESCRIPTION = "Save, list, search and delete quick notes by voice."
METADATA = {"version": "1.0.0", "capabilities": ["notes", "memory", "todo"], "permissions": [], "timeout": 10}
_FILE = Path(__file__).resolve().parent.parent / "memory" / "notes.json"

DECLARATION = {
    "name": "notes",
    "description": "Quick notes / to-dos. action=add (text), list, search (query), delete (id). Use when the user says 'note kar lo', 'yaad rakhna', 'meri notes dikhao'.",
    "parameters": {"type": "OBJECT", "properties": {
        "action": {"type": "STRING", "description": "add | list | search | delete"},
        "text": {"type": "STRING", "description": "Note text (add)"},
        "query": {"type": "STRING", "description": "Search text"},
        "id": {"type": "INTEGER", "description": "Note id (delete)"}}, "required": ["action"]},
}


def _load():
    try:
        return json.loads(_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save(rows):
    _FILE.parent.mkdir(exist_ok=True)
    _FILE.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")


def run(args, ctx):
    act = str(args.get("action", "list")).lower()
    rows = _load()
    if act == "add":
        text = str(args.get("text", "")).strip()
        if not text:
            return failed("No note saved", "the note text was empty")
        nid = max([r["id"] for r in rows], default=0) + 1
        rows.append({"id": nid, "text": text, "at": time.strftime("%Y-%m-%d %H:%M")})
        _save(rows)
        ok = any(r["id"] == nid for r in _load())
        return done(f"Note #{nid} saved", evidence="re-read from disk") if ok else failed("Note not saved", "write to notes.json failed")
    if act in ("list", "search"):
        q = str(args.get("query", "")).lower()
        hit = [r for r in rows if not q or q in r["text"].lower()]
        if not hit:
            return done("No notes found" if q else "You have no notes")
        return done(f"{len(hit)} note(s)", evidence="\n".join(f"#{r['id']} [{r['at']}] {r['text']}" for r in hit[-15:]))
    if act == "delete":
        nid = args.get("id")
        new = [r for r in rows if r["id"] != nid]
        if len(new) == len(rows):
            return failed("Nothing deleted", f"no note with id {nid}")
        _save(new)
        return done(f"Note #{nid} deleted")
    return failed("Unknown notes action", "valid: add list search delete")
