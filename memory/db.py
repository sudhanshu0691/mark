"""SQLite-backed persistent memory for MARK.

Durable, conversation-scoped storage. Every user turn and assistant reply is
written to ``conversation_turns`` inside a transaction (WAL mode) so history
survives cloud reconnects, WebSocket/frontend reconnects and application
restarts. A ``conversations`` table scopes turns to a conversation id; calls
that do not supply a conversation id fall back to the single active
conversation, keeping existing call sites working unchanged.

FTS5 index for efficient local retrieval of conversation content is created
automatically on first init if SQLite supports it.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "mark.db"
LEGACY_LONG_TERM = BASE_DIR / "memory" / "long_term.json"
LEGACY_CONVERSATION = BASE_DIR / "memory" / "conversation_history.json"

DEFAULT_CONVERSATION_TITLE = "Main"

_lock = threading.RLock()
CATEGORIES = ("identity", "preferences", "projects", "relationships", "wishes", "notes")

MAX_CONVERSATION_TURNS = 100

FTS_ENABLED = False


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def init_db() -> None:
    with _lock:
        conn = _connect()
        try:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS memory_facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                updated TEXT NOT NULL,
                UNIQUE(category, key)
            );
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                summary TEXT NOT NULL,
                language TEXT
            );
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                summary TEXT,
                language TEXT,
                is_active INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS conversation_turns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                user TEXT NOT NULL,
                assistant TEXT NOT NULL,
                channel TEXT DEFAULT 'voice',
                conversation_id INTEGER
            );
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                rating TEXT,
                text TEXT
            );
            CREATE TABLE IF NOT EXISTS task_checkpoints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                summary TEXT NOT NULL,
                status TEXT NOT NULL,
                checkpoint_at TEXT NOT NULL
            );
            """)
            _ensure_columns(conn)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_turns_conversation_id ON conversation_turns(conversation_id, id)")
            _ensure_default_conversation(conn)
            _ensure_fts(conn)
            conn.commit()
        finally:
            conn.close()
    _migrate_if_needed()


def _ensure_fts(conn: sqlite3.Connection) -> None:
    global FTS_ENABLED
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS conversation_turns_fts "
            "USING fts5(user, assistant, content='conversation_turns', content_rowid='id')"
        )
        conn.executescript("""
            CREATE TRIGGER IF NOT EXISTS conversation_turns_fts_insert
            AFTER INSERT ON conversation_turns BEGIN
                INSERT INTO conversation_turns_fts(rowid, user, assistant)
                VALUES (new.id, new.user, new.assistant);
            END;
            CREATE TRIGGER IF NOT EXISTS conversation_turns_fts_delete
            AFTER DELETE ON conversation_turns BEGIN
                INSERT INTO conversation_turns_fts(conversation_turns_fts, rowid, user, assistant)
                VALUES ('delete', old.id, old.user, old.assistant);
            END;
            CREATE TRIGGER IF NOT EXISTS conversation_turns_fts_update
            AFTER UPDATE ON conversation_turns BEGIN
                INSERT INTO conversation_turns_fts(conversation_turns_fts, rowid, user, assistant)
                VALUES ('delete', old.id, old.user, old.assistant);
                INSERT INTO conversation_turns_fts(rowid, user, assistant)
                VALUES (new.id, new.user, new.assistant);
            END;
        """)
        conn.execute("INSERT INTO conversation_turns_fts(conversation_turns_fts) VALUES ('rebuild')")
        FTS_ENABLED = True
    except sqlite3.OperationalError:
        FTS_ENABLED = False


def _ensure_columns(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(conversation_turns)")}
    if "channel" not in columns:
        conn.execute("ALTER TABLE conversation_turns ADD COLUMN channel TEXT DEFAULT 'voice'")
    if "conversation_id" not in columns:
        conn.execute("ALTER TABLE conversation_turns ADD COLUMN conversation_id INTEGER")


def _ensure_default_conversation(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT id FROM conversations WHERE is_active = 1 ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row:
        cid = row[0]
    else:
        row = conn.execute("SELECT id FROM conversations ORDER BY id DESC LIMIT 1").fetchone()
        if row:
            cid = row[0]
            conn.execute("UPDATE conversations SET is_active = 1 WHERE id = ?", (cid,))
        else:
            now = datetime.now().isoformat(timespec="seconds")
            cur = conn.execute(
                "INSERT INTO conversations(title, created_at, updated_at, is_active) "
                "VALUES (?, ?, ?, 1)",
                (DEFAULT_CONVERSATION_TITLE, now, now),
            )
            cid = cur.lastrowid
    conn.execute(
        "UPDATE conversation_turns SET conversation_id = ? WHERE conversation_id IS NULL",
        (cid,),
    )
    return cid


def set_active_conversation(conversation_id: int) -> None:
    """Mark one conversation as active and all others inactive."""
    init_db()
    with _lock:
        conn = _connect()
        try:
            conn.execute("UPDATE conversations SET is_active = 0")
            conn.execute(
                "UPDATE conversations SET is_active = 1, updated_at = ? WHERE id = ?",
                (datetime.now().isoformat(timespec="seconds"), conversation_id),
            )
            conn.commit()
        finally:
            conn.close()


def get_active_conversation() -> dict | None:
    """Return the active conversation, creating the default conversation first."""
    init_db()
    with _lock:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT id,title,created_at,updated_at,summary,language,is_active "
                "FROM conversations WHERE is_active = 1 ORDER BY id DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
    if not row:
        return None
    return {
        "id": row[0],
        "title": row[1],
        "created_at": row[2],
        "updated_at": row[3],
        "summary": row[4],
        "language": row[5],
        "is_active": bool(row[6]),
    }


def start_conversation(title: str | None = None, set_active: bool = True) -> int:
    """Create a new conversation and return its id."""
    import datetime as _dt

    init_db()
    now = _dt.datetime.now().isoformat(timespec="seconds")
    with _lock:
        conn = _connect()
        try:
            cur = conn.execute(
                "INSERT INTO conversations(title, created_at, updated_at, is_active) "
                "VALUES (?, ?, ?, ?)",
                (title or DEFAULT_CONVERSATION_TITLE, now, now, 1 if set_active else 0),
            )
            cid = cur.lastrowid
            if set_active:
                conn.execute("UPDATE conversations SET is_active = 0 WHERE id != ?", (cid,))
            conn.commit()
        finally:
            conn.close()
    return cid


def list_conversations() -> list[dict]:
    init_db()
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT id,title,created_at,updated_at,summary,language,is_active "
                "FROM conversations ORDER BY id DESC"
            ).fetchall()
        finally:
            conn.close()
    out = []
    for row in rows:
        out.append({
            "id": row[0],
            "title": row[1],
            "created_at": row[2],
            "updated_at": row[3],
            "summary": row[4],
            "language": row[5],
            "is_active": bool(row[6]),
        })
    return out


def update_conversation_summary(conversation_id: int, summary: str, language: str = "") -> None:
    import datetime as _dt

    summary = (summary or "").strip()
    if not summary:
        return
    init_db()
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                "UPDATE conversations SET summary = ?, language = ?, updated_at = ? "
                "WHERE id = ?",
                (summary[:280], language or None, _dt.datetime.now().isoformat(timespec="seconds"), conversation_id),
            )
            conn.commit()
        finally:
            conn.close()


def delete_conversation(conversation_id: int) -> bool:
    """User-controlled deletion of a conversation and all of its turns."""
    init_db()
    with _lock:
        conn = _connect()
        try:
            cur2 = conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
            deleted = cur2.rowcount > 0
            conn.execute("DELETE FROM conversation_turns WHERE conversation_id = ?", (conversation_id,))
            conn.commit()
            if deleted:
                conn2 = _connect()
                try:
                    active = conn2.execute(
                        "SELECT id FROM conversations WHERE is_active = 1 LIMIT 1"
                    ).fetchone()
                    if not active:
                        _ensure_default_conversation(conn2)
                    conn2.commit()
                finally:
                    conn2.close()
            return deleted
        finally:
            conn.close()


def _migrate_if_needed() -> None:
    import json
    import datetime as _dt
    with _lock:
        conn = _connect()
        try:
            count = conn.execute("SELECT COUNT(*) FROM memory_facts").fetchone()[0]
            if count == 0 and LEGACY_LONG_TERM.exists():
                try:
                    data = json.loads(LEGACY_LONG_TERM.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        for cat in CATEGORIES:
                            items = data.get(cat, {})
                            if not isinstance(items, dict):
                                continue
                            for key, entry in items.items():
                                val = entry.get("value") if isinstance(entry, dict) else entry
                                if not val:
                                    continue
                                updated = entry.get("updated") if isinstance(entry, dict) else None
                                conn.execute(
                                    "INSERT OR REPLACE INTO memory_facts(category,key,value,updated) VALUES (?,?,?,?)",
                                    (cat, str(key), str(val)[:380], str(updated or _dt.datetime.now().strftime("%Y-%m-%d"))),
                                )
                        sessions = data.get("sessions", [])
                        if isinstance(sessions, list):
                            for s in sessions:
                                if isinstance(s, dict) and s.get("summary"):
                                    conn.execute(
                                        "INSERT INTO sessions(date,summary,language) VALUES (?,?,?)",
                                        (s.get("date", _dt.datetime.now().strftime("%Y-%m-%d")), str(s["summary"])[:280], s.get("language")),
                                    )
                        conn.commit()
                        try:
                            LEGACY_LONG_TERM.rename(LEGACY_LONG_TERM.with_suffix(".json.migrated"))
                        except Exception:
                            pass
                except Exception as e:
                    print(f"[db] migration long_term failed: {e}")
            ccount = conn.execute("SELECT COUNT(*) FROM conversation_turns").fetchone()[0]
            if ccount == 0 and LEGACY_CONVERSATION.exists():
                try:
                    hist = json.loads(LEGACY_CONVERSATION.read_text(encoding="utf-8"))
                    if isinstance(hist, list):
                        for item in hist:
                            if isinstance(item, dict) and isinstance(item.get("user"), str) and isinstance(item.get("assistant"), str):
                                conn.execute(
                                    "INSERT INTO conversation_turns(timestamp,user,assistant) VALUES (?,?,?)",
                                    (item.get("timestamp", _dt.datetime.now().isoformat(timespec="seconds")), item["user"].strip(), item["assistant"].strip()),
                                )
                        conn.commit()
                        try:
                            LEGACY_CONVERSATION.rename(LEGACY_CONVERSATION.with_suffix(".json.migrated"))
                        except Exception:
                            pass
                except Exception as e:
                    print(f"[db] migration conversation failed: {e}")
        finally:
            conn.close()

def load_memory_dict() -> dict:
    import datetime as _dt
    init_db()
    out = {c: {} for c in CATEGORIES}
    out["sessions"] = []
    with _lock:
        conn = _connect()
        try:
            for cat, key, value, updated in conn.execute("SELECT category,key,value,updated FROM memory_facts"):
                out.setdefault(cat, {})[key] = {"value": value, "updated": updated}
            for date, summary, language in conn.execute("SELECT date,summary,language FROM sessions ORDER BY id"):
                entry = {"date": date, "summary": summary}
                if language:
                    entry["language"] = language
                out["sessions"].append(entry)
        finally:
            conn.close()
    return out


def save_memory_dict(memory: dict) -> None:
    import json as _json
    import datetime as _dt
    init_db()
    with _lock:
        conn = _connect()
        try:
            conn.execute("DELETE FROM memory_facts")
            conn.execute("DELETE FROM sessions")
            for cat in CATEGORIES:
                items = memory.get(cat, {}) if isinstance(memory, dict) else {}
                if not isinstance(items, dict):
                    continue
                for key, entry in items.items():
                    val = entry.get("value") if isinstance(entry, dict) else entry
                    if not val:
                        continue
                    updated = entry.get("updated") if isinstance(entry, dict) else None
                    conn.execute(
                        "INSERT OR REPLACE INTO memory_facts(category,key,value,updated) VALUES (?,?,?,?)",
                        (cat, str(key), str(val)[:380], str(updated or _dt.datetime.now().strftime("%Y-%m-%d"))),
                    )
            sessions = memory.get("sessions", []) if isinstance(memory, dict) else []
            if isinstance(sessions, list):
                for s in sessions[-3:]:
                    if isinstance(s, dict) and s.get("summary"):
                        conn.execute(
                            "INSERT INTO sessions(date,summary,language) VALUES (?,?,?)",
                            (s.get("date", _dt.datetime.now().strftime("%Y-%m-%d")), str(s["summary"])[:280], s.get("language")),
                        )
            conn.commit()
        finally:
            conn.close()
    try:
        mirror = BASE_DIR / "memory" / "long_term.json"
        mirror.parent.mkdir(parents=True, exist_ok=True)
        mirror.write_text(_json.dumps(memory, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

def upsert_fact(category, key, value):
    import datetime as _dt
    init_db()
    if category not in CATEGORIES:
        category = "notes"
    value = (value or "").strip()[:380]
    if not value:
        return False
    with _lock:
        conn = _connect()
        try:
            cur = conn.execute(
                "INSERT INTO memory_facts(category,key,value,updated) VALUES (?,?,?,?) "
                "ON CONFLICT(category,key) DO UPDATE SET value=excluded.value, updated=excluded.updated "
                "WHERE value <> excluded.value",
                (category, key, value, _dt.datetime.now().strftime("%Y-%m-%d")),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


def delete_fact(category, key):
    init_db()
    with _lock:
        conn = _connect()
        try:
            cur = conn.execute("DELETE FROM memory_facts WHERE category=? AND key=?", (category, key))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


def save_session_summary(summary, language=""):
    import datetime as _dt
    summary = (summary or "").strip()
    if not summary:
        return
    init_db()
    with _lock:
        conn = _connect()
        try:
            conn.execute("INSERT INTO sessions(date,summary,language) VALUES (?,?,?)",
                         (_dt.datetime.now().strftime("%Y-%m-%d"), summary[:280], language or None))
            ids = [r[0] for r in conn.execute("SELECT id FROM sessions ORDER BY id")]
            for sid in ids[:-3]:
                conn.execute("DELETE FROM sessions WHERE id=?", (sid,))
            conn.commit()
        finally:
            conn.close()
    print(f"[Memory] Session saved: {summary[:60]}...")


def pop_last_session():
    init_db()
    with _lock:
        conn = _connect()
        try:
            row = conn.execute("SELECT id,date,summary,language FROM sessions ORDER BY id DESC LIMIT 1").fetchone()
            if not row:
                return None
            sid, date, summary, language = row
            conn.execute("DELETE FROM sessions WHERE id=?", (sid,))
            conn.commit()
            entry = {"date": date, "summary": summary}
            if language:
                entry["language"] = language
            return entry
        finally:
            conn.close()


def list_sessions():
    init_db()
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute("SELECT date,summary,language FROM sessions ORDER BY id").fetchall()
            out = []
            for date, summary, language in rows:
                e = {"date": date, "summary": summary}
                if language:
                    e["language"] = language
                out.append(e)
            return out
        finally:
            conn.close()


def append_conversation_turn(user, assistant, *, channel="voice", conversation_id=None):
    import datetime as _dt
    init_db()
    if conversation_id is None:
        active = get_active_conversation()
        conversation_id = active["id"] if active else None
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO conversation_turns(timestamp,user,assistant,channel,conversation_id) "
                "VALUES (?,?,?,?,?)",
                (_dt.datetime.now().isoformat(timespec="seconds"),
                 user.strip(), assistant.strip(), channel, conversation_id))
            conn.commit()
        finally:
            conn.close()


def load_conversation_turns(limit=MAX_CONVERSATION_TURNS, *, channel=None, conversation_id=None):
    init_db()
    if conversation_id is None:
        active = get_active_conversation()
        conversation_id = active["id"] if active else None
    with _lock:
        conn = _connect()
        try:
            query = "SELECT timestamp,user,assistant FROM conversation_turns"
            params = []
            clauses = []
            if channel is not None:
                clauses.append("channel=?")
                params.append(channel)
            if conversation_id is not None:
                clauses.append("conversation_id=?")
                params.append(conversation_id)
            if clauses:
                query += " WHERE " + " AND ".join(clauses)
            params.append(max(0, min(int(limit), MAX_CONVERSATION_TURNS)))
            rows = conn.execute(query + " ORDER BY id DESC LIMIT ?", params).fetchall()
            rows.reverse()
            return [{"timestamp": t, "user": u, "assistant": a} for t, u, a in rows]
        finally:
            conn.close()


def save_feedback(rating, text=""):
    import datetime as _dt
    init_db()
    with _lock:
        conn = _connect()
        try:
            conn.execute("INSERT INTO feedback(timestamp,rating,text) VALUES (?,?,?)",
                         (_dt.datetime.now().isoformat(timespec="seconds"), rating, (text or "")[:500]))
            conn.commit()
        finally:
            conn.close()


def list_feedback(limit=50):
    init_db()
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute("SELECT timestamp,rating,text FROM feedback ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            return [{"timestamp": t, "rating": r, "text": txt} for t, r, txt in rows]
        finally:
            conn.close()



def clear_conversation_turns():
    init_db()
    with _lock:
        conn = _connect()
        try:
            conn.execute("DELETE FROM conversation_turns")
            conn.commit()
        finally:
            conn.close()
