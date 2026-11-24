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
_initialized_db = None


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def init_db() -> None:
    global _initialized_db
    with _lock:
        path = DB_PATH.resolve()
        if _initialized_db is not None and path.exists():
            stat = path.stat()
            if _initialized_db == (path, stat.st_dev, stat.st_ino):
                return
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
            CREATE TABLE IF NOT EXISTS preference_observations (
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                evidence TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                PRIMARY KEY(key, value, evidence)
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
            facts_columns = {row[1] for row in conn.execute("PRAGMA table_info(memory_facts)")}
            if "source" not in facts_columns:
                conn.execute("ALTER TABLE memory_facts ADD COLUMN source TEXT NOT NULL DEFAULT 'legacy'")
            if "confidence" not in facts_columns:
                conn.execute("ALTER TABLE memory_facts ADD COLUMN confidence REAL")
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS fact_revisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
                source TEXT NOT NULL, updated TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_fact_revisions ON fact_revisions(category,key,id);
            CREATE TRIGGER IF NOT EXISTS revise_fact
            AFTER UPDATE OF value ON memory_facts
            WHEN OLD.value <> NEW.value
            BEGIN
                INSERT INTO fact_revisions(category,key,value,source,updated)
                VALUES (OLD.category,OLD.key,OLD.value,OLD.source,OLD.updated);
                DELETE FROM fact_revisions WHERE id IN (
                    SELECT id FROM fact_revisions WHERE category=OLD.category AND key=OLD.key
                    ORDER BY id DESC LIMIT -1 OFFSET 8
                );
            END;
            CREATE TRIGGER IF NOT EXISTS forget_fact_history
            AFTER DELETE ON memory_facts
            BEGIN
                DELETE FROM fact_revisions WHERE category=OLD.category AND key=OLD.key;
            END;
            """)
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_turn_request ON conversation_turns(request_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_turns_conversation_id ON conversation_turns(conversation_id, id)")
            _ensure_default_conversation(conn)
            _ensure_fts(conn)
            conn.commit()
        finally:
            conn.close()
        _migrate_if_needed()
        # Legacy migration can insert turns without a conversation ID.
        conn = _connect()
        try:
            _ensure_default_conversation(conn)
            conn.commit()
        finally:
            conn.close()
        stat = path.stat()
        _initialized_db = (path, stat.st_dev, stat.st_ino)


def _ensure_fts(conn: sqlite3.Connection) -> None:
    global FTS_ENABLED
    try:
        existed = conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?", ("conversation_turns_fts",)).fetchone()
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
        if not existed:
            conn.execute("INSERT INTO conversation_turns_fts(conversation_turns_fts) VALUES ('rebuild')")
        FTS_ENABLED = True
    except sqlite3.OperationalError:
        FTS_ENABLED = False


def _ensure_columns(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(conversation_turns)")}
    if "channel" not in columns:
        conn.execute("ALTER TABLE conversation_turns ADD COLUMN channel TEXT DEFAULT 'voice'")
    if "request_id" not in columns:
        conn.execute("ALTER TABLE conversation_turns ADD COLUMN request_id TEXT")
    if "pending" not in columns:
        conn.execute("ALTER TABLE conversation_turns ADD COLUMN pending INTEGER NOT NULL DEFAULT 0")
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
            if not conn.execute("SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)).fetchone():
                raise ValueError("Unknown conversation ID")
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
            for cat, key, value, updated, source, confidence in conn.execute(
                "SELECT category,key,value,updated,source,confidence FROM memory_facts"
            ):
                out.setdefault(cat, {})[key] = {
                    "value": value, "updated": updated, "source": source, "confidence": confidence,
                }
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
    # Validate before the replacement transaction so an invalid entry cannot
    # erase existing facts or be copied into the legacy JSON mirror.
    for cat in CATEGORIES:
        items = memory.get(cat, {}) if isinstance(memory, dict) else {}
        if isinstance(items, dict):
            for key, entry in items.items():
                value = entry.get("value") if isinstance(entry, dict) else entry
                if value and validate_fact(key, str(value)):
                    raise ValueError("Invalid or sensitive memory fact")
    init_db()
    with _lock:
        conn = _connect()
        try:
            previous = set(conn.execute("SELECT category,key FROM memory_facts"))
            retained = set()
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
                    retained.add((cat, str(key)))
                    conn.execute(
                        "INSERT INTO memory_facts(category,key,value,updated,source,confidence) "
                        "VALUES (?,?,?,?,?,?) ON CONFLICT(category,key) DO UPDATE SET "
                        "value=excluded.value,updated=excluded.updated,source=excluded.source,confidence=excluded.confidence",
                        (cat, str(key), str(val)[:380], str(updated or _dt.datetime.now().strftime("%Y-%m-%d")),
                         entry.get("source", "legacy") if isinstance(entry, dict) else "legacy",
                         entry.get("confidence") if isinstance(entry, dict) else None),
                    )
            conn.executemany("DELETE FROM memory_facts WHERE category=? AND key=?", previous - retained)
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

def validate_fact(key, value):
    """Reject likely credentials before they enter persistent learned facts."""
    import re
    from core.cloud_privacy import redact_text
    if (not isinstance(key, str) or not key.strip() or
            not isinstance(value, str) or not value.strip()):
        return "A non-empty memory key and value are required."
    sensitive = (r"password|passwd|passphrase|api[ _-]?key|access[ _-]?token|"
                 r"refresh[ _-]?token|client[ _-]?secret|private[ _-]?key|credential")
    if re.search(rf"(?i)(?:{sensitive}|authorization)", key):
        return "Credentials cannot be saved as learned facts."
    if redact_text(value) != value or re.search(rf"(?i)\b(?:{sensitive})\b", value):
        return "Credentials cannot be saved as learned facts."
    return ""


def upsert_fact(category, key, value, *, source="local"):
    init_db()
    if category not in CATEGORIES:
        category = "notes"
    if validate_fact(key, value):
        return False
    value = value.strip()[:380]
    with _lock:
        conn = _connect()
        try:
            cur = conn.execute(
                "INSERT INTO memory_facts(category,key,value,updated,source,confidence) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(category,key) DO UPDATE SET value=excluded.value, updated=excluded.updated, "
                "source=excluded.source, confidence=excluded.confidence "
                "WHERE value <> excluded.value OR source <> excluded.source",
                (category, key, value, datetime.now().isoformat(timespec="seconds"), source,
                 1.0 if source in {"user_explicit", "user_confirmed"} else None),
            )
            if category == "preferences":
                conn.execute("DELETE FROM preference_observations WHERE key=?", (key,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


def observe_preference(key, value, evidence):
    """Corroborate whitelisted observations in one transaction."""
    init_db()
    now = datetime.now().isoformat(timespec="seconds")
    with _lock:
        conn = _connect()
        try:
            with conn:
                existing = conn.execute(
                    "SELECT value FROM memory_facts WHERE category='preferences' AND key=?", (key,),
                ).fetchone()
                if existing:
                    return "duplicate" if existing[0] == value else "contradiction"
                conn.execute(
                    "DELETE FROM preference_observations WHERE observed_at < datetime('now', '-60 days')",
                )
                conn.execute(
                    "INSERT OR IGNORE INTO preference_observations VALUES (?,?,?,?)",
                    (key, value, evidence, now),
                )
                variants = conn.execute(
                    "SELECT value, count(*) FROM preference_observations WHERE key=? GROUP BY value",
                    (key,),
                ).fetchall()
                if len(variants) > 1:
                    return "contradiction"
                if variants[0][1] < 2:
                    return "provisional"
                conn.execute(
                    "INSERT INTO memory_facts(category,key,value,updated,source,confidence) "
                    "VALUES ('preferences',?,?,?,'observed_user_preference',0.8)",
                    (key, value, now),
                )
                conn.execute("DELETE FROM preference_observations WHERE key=?", (key,))
                return "saved"
        finally:
            conn.close()


def delete_fact(category, key):
    init_db()
    with _lock:
        conn = _connect()
        try:
            cur = conn.execute("DELETE FROM memory_facts WHERE category=? AND key=?", (category, key))
            if category == "preferences":
                conn.execute("DELETE FROM preference_observations WHERE key=?", (key,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


def fact_history(category, key):
    """Return prior values for deliberate local review."""
    from contextlib import closing

    init_db()
    with _lock, closing(_connect()) as conn:
        return conn.execute(
            "SELECT value,source,updated FROM fact_revisions "
            "WHERE category=? AND key=? ORDER BY id DESC", (category, key),
        ).fetchall()


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
            if conversation_id is not None and not conn.execute("SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)).fetchone():
                raise ValueError("Conversation was deleted or does not exist")
            conn.execute(
                "INSERT INTO conversation_turns(timestamp,user,assistant,channel,conversation_id) "
                "VALUES (?,?,?,?,?)",
                (_dt.datetime.now().isoformat(timespec="seconds"),
                 user.strip(), assistant.strip(), channel, conversation_id))
            conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?",
                         (_dt.datetime.now().isoformat(timespec="seconds"), conversation_id))
            conn.commit()
        finally:
            conn.close()


def checkpoint_conversation_turn(request_id, user, assistant="", *, channel, conversation_id, complete=False):
    """Persist a transcript snapshot and replace it atomically with its final turn."""
    init_db()
    now = datetime.now().isoformat(timespec="seconds")
    user = user.strip()
    if not user:
        return False
    if not complete:
        assistant = "[Interrupted or unfinished] " + assistant.strip()
    with _lock:
        conn = _connect()
        try:
            with conn:
                cursor = conn.execute(
                    "INSERT INTO conversation_turns(timestamp,user,assistant,channel,conversation_id,request_id,pending) "
                    "SELECT ?,?,?,?,?,?,? WHERE EXISTS(SELECT 1 FROM conversations WHERE id=?) "
                    "ON CONFLICT(request_id) DO UPDATE SET user=excluded.user, assistant=excluded.assistant, "
                    "pending=excluded.pending WHERE conversation_turns.pending=1 "
                    "AND conversation_turns.conversation_id=excluded.conversation_id",
                    (now, user, assistant, channel, conversation_id, request_id, not complete, conversation_id),
                )
                if cursor.rowcount:
                    conn.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now, conversation_id))
                return cursor.rowcount > 0
        finally:
            conn.close()


def load_conversation_turns(limit=MAX_CONVERSATION_TURNS, *, channel=None, conversation_id=None, include_pending=True):
    init_db()
    if conversation_id is None:
        active = get_active_conversation()
        conversation_id = active["id"] if active else None
    with _lock:
        conn = _connect()
        try:
            query = "SELECT timestamp,user,assistant FROM conversation_turns"
            params = []
            clauses = [] if include_pending else ["pending=0"]
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


def search_conversation_turns(query: str, *, conversation_id=None, limit=5, include_pending=True):
    """Search only within one conversation, without sending data to a provider."""
    import re
    init_db()
    if conversation_id is None:
        active = get_active_conversation()
        conversation_id = active["id"] if active else None
    terms = list(dict.fromkeys(re.findall(r"\w{3,}", query.casefold())))[:12]
    if not terms or conversation_id is None:
        return []
    limit = max(1, min(int(limit), 20))
    with _lock:
        conn = _connect()
        try:
            if FTS_ENABLED:
                expression = " OR ".join('"' + term + '"' for term in terms)
                try:
                    rows = conn.execute(
                        "SELECT t.id,t.timestamp,t.user,t.assistant FROM conversation_turns_fts "
                        "JOIN conversation_turns t ON t.id=conversation_turns_fts.rowid "
                        "WHERE conversation_turns_fts MATCH ? AND t.conversation_id=? AND (? OR t.pending=0) "
                        "ORDER BY bm25(conversation_turns_fts),t.id DESC LIMIT ?",
                        (expression, conversation_id, include_pending, limit)).fetchall()
                    return [{"id": i, "timestamp": ts, "user": u, "assistant": a} for i,ts,u,a in rows]
                except sqlite3.OperationalError:
                    pass
            clauses = " OR ".join("(lower(user) LIKE ? OR lower(assistant) LIKE ?)" for _ in terms)
            params = [conversation_id, include_pending]
            for term in terms:
                params.extend(["%" + term + "%"] * 2)
            rows = conn.execute(
                "SELECT id,timestamp,user,assistant FROM conversation_turns "
                "WHERE conversation_id=? AND (? OR pending=0) AND (" + clauses + ") ORDER BY id DESC LIMIT ?", (*params, limit)).fetchall()
            return [{"id": i, "timestamp": ts, "user": u, "assistant": a} for i,ts,u,a in rows]
        finally:
            conn.close()
