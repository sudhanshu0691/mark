"""SQLite-backed vision event history."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


class EventStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS vision_events (id INTEGER PRIMARY KEY, timestamp TEXT NOT NULL, event_type TEXT NOT NULL, camera_name TEXT NOT NULL, payload TEXT NOT NULL)")
            db.execute("CREATE INDEX IF NOT EXISTS ix_vision_events_timestamp ON vision_events(timestamp DESC)")

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def add(self, event: dict[str, Any]) -> int:
        with self._connect() as db:
            cursor = db.execute("INSERT INTO vision_events(timestamp,event_type,camera_name,payload) VALUES(?,?,?,?)",
                                (event["timestamp"], event["event_type"], event.get("camera_name", ""), json.dumps(event, ensure_ascii=False)))
            return int(cursor.lastrowid)

    def history(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT id,payload FROM vision_events ORDER BY id DESC LIMIT ?", (max(1, min(int(limit), 1000)),)).fetchall()
        return [{"id": row["id"], **json.loads(row["payload"])} for row in rows]

    def count(self, event_type: str = "person_detected") -> int:
        with self._connect() as db:
            return int(db.execute("SELECT COUNT(*) FROM vision_events WHERE event_type=?", (event_type,)).fetchone()[0])

    def latest_timestamp(self, event_type: str = "person_detected") -> str | None:
        with self._connect() as db:
            row = db.execute("SELECT timestamp FROM vision_events WHERE event_type=? ORDER BY id DESC LIMIT 1", (event_type,)).fetchone()
            return str(row[0]) if row else None

    def prune(self, retention_days: int) -> int:
        with self._connect() as db:
            cursor = db.execute("DELETE FROM vision_events WHERE julianday(timestamp) < julianday('now', ?)", (f"-{max(1, int(retention_days))} days",))
            return cursor.rowcount
