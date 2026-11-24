"""Encrypted, leased checkpoints for specialist dependency graphs."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from uuid import uuid4

_PATH = Path(__file__).resolve().parents[2] / "data" / "workflows.db"
_SAFE_READS = frozenset({"runtime_status", "system_status"})


def may_retry(task):
    return not task.tool or (task.tool in _SAFE_READS and not task.arguments)


def _process_start(pid):
    import psutil

    try:
        return psutil.Process(pid).create_time()
    except psutil.NoSuchProcess:
        return 0


def _crypt(payload, *, decrypt, key_path):
    if os.name == "nt":
        import win32crypt

        if decrypt:
            return win32crypt.CryptUnprotectData(payload, None, None, None, 1)[1]
        return win32crypt.CryptProtectData(
            payload, "MARK workflow", None, None, None, 1
        )

    from cryptography.fernet import Fernet

    try:
        if decrypt:
            raise FileExistsError
        # Exclusive secure creation means concurrent instances never replace the key.
        descriptor = os.open(key_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(Fernet.generate_key())
    except FileExistsError:
        pass
    if key_path.is_symlink() or key_path.stat().st_mode & 0o077:
        raise PermissionError("Workflow recovery key permissions are unsafe.")
    cipher = Fernet(key_path.read_bytes())
    return cipher.decrypt(payload) if decrypt else cipher.encrypt(payload)


class WorkflowJournal:
    def __init__(self, path=None):
        self.path = Path(path) if path else _PATH
        self.key_path = self.path.with_suffix(".key")
        self.identifier = None
        self.pid = os.getpid()
        self.owner_start = _process_start(self.pid)
        self.snapshot = None

    def _connection(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=4)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS workflows (id TEXT PRIMARY KEY, "
            "payload BLOB NOT NULL, status TEXT NOT NULL, pid INTEGER NOT NULL, started REAL NOT NULL, "
            "updated REAL NOT NULL)"
        )
        return connection

    def _encode(self):
        content = json.dumps(self.snapshot, ensure_ascii=False).encode()
        if len(content) > 200_000:
            raise ValueError("Workflow recovery budget exceeded.")
        return _crypt(content, decrypt=False, key_path=self.key_path)

    def create(self, graph):
        self.identifier = uuid4().hex[:12]
        self.snapshot = {"graph": graph, "running": None, "outcomes": {}}
        with closing(self._connection()) as connection, connection:
            connection.execute(
                "INSERT INTO workflows VALUES (?,?,'active',?,?,?)",
                (
                    self.identifier,
                    self._encode(),
                    self.pid,
                    self.owner_start,
                    time.time(),
                ),
            )
            connection.execute(
                "DELETE FROM workflows WHERE status='completed' AND updated < ?",
                (time.time() - 7 * 86400,),
            )
        return self.identifier

    def claim(self, identifier):
        with closing(self._connection()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload,status,pid,started FROM workflows WHERE id=?",
                (identifier,),
            ).fetchone()
            if not row or row[1] == "completed":
                raise ValueError("No recoverable workflow has that ID.")
            if row[1] == "active" and _process_start(row[2]) == row[3]:
                raise ValueError("This workflow is already running.")
            self.snapshot = json.loads(
                _crypt(row[0], decrypt=True, key_path=self.key_path)
            )
            connection.execute(
                "UPDATE workflows SET status='active',pid=?,started=?,updated=? WHERE id=?",
                (self.pid, self.owner_start, time.time(), identifier),
            )
        self.identifier = identifier
        return self.snapshot

    def write(self, *, running=None, outcomes=None, status="active"):
        if outcomes is not None:
            self.snapshot["outcomes"] = {
                key: {"status": value.status, "text": value.text}
                for key, value in outcomes.items()
            }
        self.snapshot["running"] = running
        with closing(self._connection()) as connection, connection:
            count = connection.execute(
                "UPDATE workflows SET payload=?,status=?,updated=? "
                "WHERE id=? AND pid=? AND started=? AND status='active'",
                (
                    self._encode(),
                    status,
                    time.time(),
                    self.identifier,
                    self.pid,
                    self.owner_start,
                ),
            ).rowcount
            if count != 1:
                raise RuntimeError("Workflow lease no longer belongs to this runner.")

    def recoverable(self):
        with closing(self._connection()) as connection:
            rows = connection.execute(
                "SELECT id,status,pid,started FROM workflows WHERE status <> 'completed' "
                "ORDER BY updated DESC LIMIT 30"
            ).fetchall()
        return [
            row[0]
            for row in rows
            if row[1] == "paused" or _process_start(row[2]) != row[3]
        ]
