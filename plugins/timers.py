"""In-process timers / stopwatch: Mark speaks when time is up (use `reminder` for things that must survive a restart)."""
import threading
import time

from core.result import done, failed

NAME = "timers"
DESCRIPTION = "Countdown timers that speak when finished."
METADATA = {"version": "1.0.0", "capabilities": ["timer", "alarm", "countdown"], "permissions": [], "timeout": 10}
_T: dict[int, dict] = {}
_N = [0]
_LOCK = threading.Lock()

DECLARATION = {
    "name": "timer",
    "description": "Countdown timer. action=start (minutes and/or seconds, label), list, cancel (id or all). Mark speaks when it ends. For '10 minute ka timer lagao'.",
    "parameters": {"type": "OBJECT", "properties": {
        "action": {"type": "STRING", "description": "start | list | cancel"},
        "minutes": {"type": "NUMBER", "description": "Minutes"}, "seconds": {"type": "NUMBER", "description": "Seconds"},
        "label": {"type": "STRING", "description": "What the timer is for"},
        "id": {"type": "INTEGER", "description": "Timer id for cancel (omit = cancel all)"}}, "required": ["action"]},
}


def _left(t):
    return max(0, int(t["end"] - time.time()))


def run(args, ctx):
    act = str(args.get("action", "list")).lower()
    if act == "start":
        secs = float(args.get("minutes") or 0) * 60 + float(args.get("seconds") or 0)
        if secs <= 0 or secs > 86400:
            return failed("Timer not started", "give a duration between 1 second and 24 hours")
        label = str(args.get("label") or "timer")
        with _LOCK:
            _N[0] += 1
            tid = _N[0]

        def fire():
            with _LOCK:
                _T.pop(tid, None)
            ctx.log(f"timer {tid} finished: {label}")
            ctx.speak(f"[TIMER] The timer '{label}' has just finished. Tell the user in one short sentence.")

        th = threading.Timer(secs, fire)
        th.daemon = True
        with _LOCK:
            _T[tid] = {"label": label, "end": time.time() + secs, "th": th}
        th.start()
        return done(f"Timer #{tid} '{label}' set for {int(secs // 60)}m {int(secs % 60)}s", evidence="timer thread running")
    if act == "list":
        with _LOCK:
            rows = [f"#{i} '{t['label']}' - {_left(t) // 60}m {_left(t) % 60}s left" for i, t in _T.items()]
        return done(f"{len(rows)} active timer(s)", evidence="\n".join(rows))
    if act == "cancel":
        with _LOCK:
            ids = [args["id"]] if args.get("id") in _T else ([] if args.get("id") else list(_T))
            for i in ids:
                _T.pop(i)["th"].cancel()
        return done(f"Cancelled {len(ids)} timer(s)") if ids else failed("Nothing cancelled", "no such timer")
    return failed("Unknown timer action", "valid: start list cancel")


def on_unload(ctx):
    with _LOCK:
        for t in _T.values():
            t["th"].cancel()
        _T.clear()
