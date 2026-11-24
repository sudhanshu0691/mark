"""Thread-safe task lifecycle, checkpoint, event and audit service."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections import deque
from contextlib import closing
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from core.agents import for_tool

_EVENT_LOG = Path(__file__).resolve().parent.parent / "data" / "tasks.jsonl"


class TaskManager:
    """Tracks task state while keeping private argument values out of audit logs."""
    def __init__(self, capacity: int = 500, *, database: Path | None = None):
        self._lock = threading.RLock()
        self._rows: dict[str, dict[str, Any]] = {}
        self._capacity = max(1, capacity)
        self._order: deque[str] = deque()
        self._listeners: list[Callable[[dict], None]] = []
        self._cancelled: set[str] = set()
        self._database = database
        self._restored = False

    def _connect(self):
        self._database.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._database, timeout=5)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS task_state (
                id TEXT PRIMARY KEY, updated_at REAL NOT NULL, state TEXT NOT NULL
            )
        """)
        return conn

    def _restore(self):
        if self._database is None or self._restored:
            return
        try:
            with closing(self._connect()) as conn, conn:
                rows = conn.execute(
                    "SELECT state FROM task_state ORDER BY updated_at DESC LIMIT ?", (self._capacity,)
                ).fetchall()
                for (payload,) in reversed(rows):
                    row = json.loads(payload)
                    if row["status"] not in {"DONE", "FAILED", "CANCELLED", "UNVERIFIED"}:
                        row.update(status="UNVERIFIED", execution_status="UNVERIFIED",
                                   verification_status="UNVERIFIED", recovered=True)
                        conn.execute("UPDATE task_state SET state=? WHERE id=?", (json.dumps(row), row["id"]))
                    self._rows[row["id"]] = row
                    self._order.append(row["id"])
            self._restored = True
        except (OSError, sqlite3.Error, ValueError):
            self._restored = True
            self._publish({"event": "task_state_unavailable"})

    def _persist(self, row):
        if self._database is None:
            return
        # Checkpoint labels and result lines may contain data copied from local tools.
        stored = {k: v for k, v in row.items() if k not in {"result_summary", "checkpoints"}}
        stored["checkpoints"] = [
            {"at": item["at"], "status": item["status"]}
            for item in row.get("checkpoints", [])
        ]
        try:
            with closing(self._connect()) as conn, conn:
                conn.execute(
                    "INSERT INTO task_state VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE "
                    "SET updated_at=excluded.updated_at, state=excluded.state",
                    (row["id"], row["updated_at"], json.dumps(stored)),
                )
                conn.execute(
                    "DELETE FROM task_state WHERE id IN "
                    "(SELECT id FROM task_state ORDER BY updated_at DESC LIMIT -1 OFFSET ?)",
                    (self._capacity,),
                )
        except (OSError, sqlite3.Error):
            self._publish({"event": "task_state_unavailable", "id": row["id"]})

    def subscribe(self, callback: Callable[[dict], None]) -> None:
        with self._lock:
            self._listeners.append(callback)

    def _publish(self, event: dict) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for callback in listeners:
            try:
                callback(dict(event))
            except Exception:
                pass

    def _record(self, event: dict) -> None:
        try:
            _EVENT_LOG.parent.mkdir(parents=True, exist_ok=True)
            with _EVENT_LOG.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
        except OSError:
            pass
        self._publish(event)

    def start(self, task_id: str | int, tool: str, args: dict | None = None) -> dict:
        key = str(task_id)
        now = time.time()
        agent = for_tool(tool).name
        with self._lock:
            self._restore()
            if key in self._rows:
                raise ValueError("Task ID already exists.")
            while len(self._order) >= self._capacity:
                expired = self._order.popleft()
                self._rows.pop(expired, None)
                self._cancelled.discard(expired)
            row = {"id": key, "tool": str(tool), "agent": agent, "status": "RUNNING",
                   "started_at": now, "updated_at": now, "attempts": 1,
                   "argument_names": sorted(str(k) for k in (args or {})),
                   "task_id": key, "action_id": key, "execution_status": "RUNNING",
                   "verification_status": "PENDING", "approval_required": False}
            self._rows[key] = row
            self._order.append(key)
            self._persist(row)
        event = {"event": "task_started", "id": key, "tool": str(tool), "agent": agent,
                 "at": now, "argument_names": row["argument_names"]}
        self._record(event)
        return dict(row)

    def set_approval_required(self, task_id, required):
        with self._lock:
            row = self._rows.get(str(task_id))
            if row:
                row["approval_required"] = bool(required)
                self._persist(row)

    def stage(self, task_id, state):
        key = str(task_id)
        with self._lock:
            row = self._rows.get(key)
            if not row or row["status"] == state:
                return
            row.update(status=state, updated_at=time.time())
            self._persist(row)
        self._record({"event":"task_stage", "id":key, "status":state, "at":time.time()})

    def checkpoint(self, task_id: str | int, label: str, status: str = "DONE") -> None:
        key, now = str(task_id), time.time()
        event = {"event": "checkpoint", "id": key, "label": str(label)[:100],
                 "status": str(status).upper(), "at": now}
        with self._lock:
            row = self._rows.get(key)
            if row:
                row["updated_at"] = now
                row["attempts"] = int(row.get("attempts", 1))
                row.setdefault("checkpoints", []).append({"label": event["label"], "status": event["status"], "at": now})
                del row["checkpoints"][:-30]
                self._persist(row)
        event.pop("label")
        self._record(event)

    def finish(self, task_id: str | int, result: str, verification: str = "UNVERIFIED") -> None:
        key, now = str(task_id), time.time()
        text = str(result)
        if text.lstrip().startswith("[DONE]"):
            status = "DONE"
        elif text.lstrip().startswith("[FAILED]"):
            status = "FAILED"
        elif text.lstrip().startswith("[CANCELLED]"):
            status = "CANCELLED"
        elif text.lstrip().upper().startswith("RESULT: SUCCESS"):
            status = "DONE"
        elif text.lstrip().upper().startswith("RESULT: FAILED"):
            status = "FAILED"
        elif text.lstrip().upper().startswith("RESULT: CANCELLED"):
            status = "CANCELLED"
        else:
            status = "UNVERIFIED"
        if verification == "FAIL" and status == "DONE":
            status = "FAILED"
        with self._lock:
            row = self._rows.get(key)
            if row:
                row.update(status=status, updated_at=now, verification=str(verification).upper(),
                           execution_status=("FAILED" if status == "FAILED" else "NOT_STARTED" if status == "CANCELLED" else "EXECUTED"),
                           verification_status={"PASS": "VERIFIED", "FAIL": "FAILED"}.get(str(verification).upper(), "UNVERIFIED"),
                           result_summary=(text.splitlines() or [""])[0][:240])
                self._persist(row)
            self._cancelled.discard(key)
        event = {"event": "task_finished", "id": key, "status": status,
                 "verification": str(verification).upper(), "at": now}
        self._record(event)

    def request_cancel(self, task_id: str | int) -> None:
        key = str(task_id)
        with self._lock:
            if key in self._rows:
                self._cancelled.add(key)
        self._record({"event": "cancel_requested", "id": key, "at": time.time()})

    def is_cancelled(self, task_id: str | int) -> bool:
        with self._lock:
            return str(task_id) in self._cancelled

    def recent(self, limit: int = 50) -> list[dict]:
        with self._lock:
            self._restore()
            if limit <= 0:
                return []
            return [deepcopy(self._rows[key]) for key in list(self._order)[-limit:]]


TASKS = TaskManager(database=_EVENT_LOG.with_suffix(".db"))

