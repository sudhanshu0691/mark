"""See what eats CPU / RAM and close a process (asks for confirmed=true before killing)."""
from core.result import done, failed, unverified

NAME = "process_manager"
DESCRIPTION = "Top CPU/RAM processes, find a process, kill a process."
METADATA = {"version": "1.0.0", "capabilities": ["processes", "cpu", "ram", "kill"], "permissions": [], "timeout": 25}
_PROTECTED = {"system", "csrss.exe", "wininit.exe", "winlogon.exe", "services.exe", "lsass.exe", "smss.exe", "explorer.exe", "svchost.exe"}

DECLARATION = {
    "name": "processes",
    "description": "action=top (heaviest by cpu or ram), find (name), kill (name or pid; set confirmed=true only after the user agreed).",
    "parameters": {"type": "OBJECT", "properties": {
        "action": {"type": "STRING", "description": "top | find | kill"},
        "sort": {"type": "STRING", "description": "cpu | ram (top)"}, "name": {"type": "STRING", "description": "Process name"},
        "pid": {"type": "INTEGER", "description": "Process id"}, "count": {"type": "INTEGER", "description": "How many rows"},
        "confirmed": {"type": "BOOLEAN", "description": "kill: user explicitly confirmed"}}, "required": ["action"]},
}


def _procs():
    import psutil
    psutil.cpu_percent(None)
    rows = []
    for p in psutil.process_iter(["pid", "name", "memory_info"]):
        try:
            rows.append((p.info["name"] or "?", p.info["pid"], p.cpu_percent(None), (p.info["memory_info"].rss if p.info["memory_info"] else 0) / 1048576))
        except Exception:
            continue
    return rows


def run(args, ctx):
    try:
        import psutil
    except ImportError:
        return failed("processes unavailable", "pip install psutil")
    act = str(args.get("action", "top")).lower()
    n = int(args.get("count") or 8)
    if act == "top":
        import time
        _procs(); time.sleep(0.6)
        rows = _procs()
        key = 2 if str(args.get("sort", "ram")).lower() == "cpu" else 3
        top = sorted(rows, key=lambda r: r[key], reverse=True)[:n]
        return done(f"Top {len(top)} by {'CPU' if key == 2 else 'RAM'}", evidence="\n".join(f"{r[0]} (pid {r[1]}): cpu {r[2]:.0f}%  ram {r[3]:.0f} MB" for r in top))
    name = str(args.get("name", "")).lower().replace(".exe", "")
    if act == "find":
        hit = [r for r in _procs() if name and name in r[0].lower()]
        return done(f"{len(hit)} process(es) match '{name}'", evidence="\n".join(f"{r[0]} pid {r[1]} ram {r[3]:.0f} MB" for r in hit[:15])) if hit else failed(f"'{name}' is not running", "no matching process")
    if act == "kill":
        targets = [psutil.Process(args["pid"])] if args.get("pid") else [psutil.Process(r[1]) for r in _procs() if name and name in r[0].lower()]
        if not targets:
            return failed("Nothing to kill", f"no process matches '{name or args.get('pid')}'")
        bad = [t.name() for t in targets if t.name().lower() in _PROTECTED]
        if bad:
            return failed("Refused", f"{bad[0]} is a protected system process")
        if not args.get("confirmed"):
            return failed("Not killed - needs confirmation", f"ask the user first, then call again with confirmed=true ({len(targets)} process(es): {targets[0].name()})")
        for t in targets:
            try:
                t.terminate()
            except Exception:
                pass
        psutil.wait_procs(targets, timeout=3)
        left = [t for t in targets if t.is_running()]
        for t in left:
            try:
                t.kill()
            except Exception:
                pass
        psutil.wait_procs(left, timeout=2)
        still = [t for t in targets if t.is_running()]
        return done(f"Closed {len(targets) - len(still)} process(es)", evidence="re-checked: none running") if not still else unverified("Some processes survived", reason="may need admin rights (run_as_admin.bat)")
    return failed("Unknown processes action", "valid: top find kill")
