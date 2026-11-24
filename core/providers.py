"""Process-owned cloud transports and anonymous runtime diagnostics."""
from __future__ import annotations

from threading import RLock
from time import monotonic

_lock = RLock()
_clients = {}
_stats = {}


from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date
from pathlib import Path
import sqlite3
from threading import BoundedSemaphore

_slots = BoundedSemaphore(2)
_task_usage = ContextVar("mark_cloud_task", default=None)
USAGE_PATH = Path(__file__).resolve().parent.parent / "data" / "cloud_usage.db"


class BudgetExceeded(RuntimeError):
    pass


@contextmanager
def task_budget():
    token = _task_usage.set({})
    try:
        yield
    finally:
        _task_usage.reset(token)


def reserve(name, cfg):
    """Count submitted requests, including each retry, across MARK restarts."""
    limits = cfg.get("cloud_limits", {})
    per_task = int(limits.get("requests_per_task", 0))
    daily = int(limits.get("requests_per_day", 0))
    usage = _task_usage.get()
    if per_task and usage is not None and usage.get(name, 0) >= per_task:
        raise BudgetExceeded(f"{name} reached the configured per-task request budget.")
    if daily:
        day = date.today().isoformat()
        USAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(USAGE_PATH, timeout=5)
        try:
            with conn:
                conn.execute("CREATE TABLE IF NOT EXISTS usage(day TEXT, provider TEXT, n INTEGER, PRIMARY KEY(day,provider))")
                conn.execute("INSERT OR IGNORE INTO usage VALUES (?,?,0)", (day,name))
                changed = conn.execute("UPDATE usage SET n=n+1 WHERE day=? AND provider=? AND n<?",
                                       (day, name, daily)).rowcount
                if not changed:
                    raise BudgetExceeded(f"{name} reached the configured daily request budget.")
                conn.execute("DELETE FROM usage WHERE day < date('now', '-31 day')")
        finally:
            conn.close()
    if usage is not None:
        usage[name] = usage.get(name, 0) + 1


@contextmanager
def cloud_request(name, cfg):
    if not _slots.acquire(timeout=5):
        raise BudgetExceeded("Cloud workers are busy.")
    try:
        reserve(name, cfg)
        yield
    finally:
        _slots.release()


def initialize(name, cfg):
    from core.secrets import get_secret
    key = get_secret(name)
    with _lock:
        status = _stats.setdefault(name, {"state": "INITIALIZING", "requests": 0, "tokens": 0})
    if not key:
        with _lock:
            status["state"] = "NOT_CONFIGURED"
        return "NOT_CONFIGURED"
    try:
        if name == "gemini":
            from google import genai
            client = genai.Client(api_key=key, http_options={"timeout": int(cfg["gemini"].get("timeout_s", 60) * 1000)})
        elif name == "groq":
            import importlib
            Groq = importlib.import_module("groq").Groq
            client = Groq(api_key=key, timeout=float(cfg.get("timeout_s", 60)), max_retries=0)
        else:
            raise ValueError("Unknown provider")
    except Exception:
        with _lock:
            status["state"] = "FAILED"
        return "FAILED"
    with _lock:
        _clients[name] = (key, client)
        status["state"] = "CONFIGURED"
    return "CONFIGURED"


def client_for(name, key):
    with _lock:
        entry = _clients.get(name)
        return entry[1] if entry and entry[0] == key else None


def record(name, model, start, *, status=200, tokens=0, first_ms=None):
    with _lock:
        item = _stats.setdefault(name, {"requests": 0, "tokens": 0})
        item["requests"] += 1
        item["tokens"] += tokens or 0
        item.update(state="READY" if status == 200 else "RATE_LIMITED" if status == 429 else "DEGRADED",
                    model=model, latency_ms=round((monotonic()-start)*1000), last_status=status)
        if first_ms is not None:
            item["first_text_ms"] = first_ms
        if status != 200:
            item["failures"] = item.get("failures", 0) + 1


def snapshot():
    with _lock:
        return {name: dict(value) for name, value in _stats.items()}


def close():
    with _lock:
        clients = list(_clients.values())
        _clients.clear()
        for item in _stats.values():
            item["state"] = "STOPPED"
    for _, client in clients:
        client.close()
