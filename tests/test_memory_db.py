import sys, tempfile, threading
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

def test_memory_roundtrip(tmp_path, monkeypatch):
    import memory.db as db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(db, "LEGACY_LONG_TERM", tmp_path / "no_long.json")
    monkeypatch.setattr(db, "LEGACY_CONVERSATION", tmp_path / "no_conv.json")
    db.init_db()
    db.upsert_fact("notes", "k", "v")
    mem = db.load_memory_dict()
    assert mem["notes"]["k"]["value"] == "v"
    db.save_session_summary("s")
    assert db.pop_last_session()["summary"] == "s"
    db.append_conversation_turn("hi", "hello")
    turns = db.load_conversation_turns()
    assert turns and turns[-1]["user"] == "hi"
    db.save_feedback("up", "nice")
    assert db.list_feedback()[0]["rating"] == "up"


def test_concurrent_writes(tmp_path, monkeypatch):
    import memory.db as db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t2.db")
    monkeypatch.setattr(db, "LEGACY_LONG_TERM", tmp_path / "no_long.json")
    monkeypatch.setattr(db, "LEGACY_CONVERSATION", tmp_path / "no_conv.json")
    db.init_db()
    def worker(i):
        db.upsert_fact("notes", f"k{i}", f"v{i}")
    ts = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in ts: t.start()
    for t in ts: t.join()
    mem = db.load_memory_dict()
    assert len(mem["notes"]) == 20


def test_conversation_history_is_not_pruned_or_truncated(tmp_path, monkeypatch):
    import sqlite3
    import memory.db as db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "history.db")
    monkeypatch.setattr(db, "LEGACY_LONG_TERM", tmp_path / "missing-long.json")
    monkeypatch.setattr(db, "LEGACY_CONVERSATION", tmp_path / "missing-history.json")
    long_text = "x" * 13000
    db.append_conversation_turn(long_text, "first")
    for i in range(100):
        db.append_conversation_turn(str(i), "answer")
    with sqlite3.connect(db.DB_PATH) as conn:
        assert conn.execute("SELECT COUNT(*) FROM conversation_turns").fetchone()[0] == 101
        assert conn.execute("SELECT user FROM conversation_turns ORDER BY id LIMIT 1").fetchone()[0] == long_text
    assert len(db.load_conversation_turns()) == 100


def test_conversation_search_is_scoped(tmp_path, monkeypatch):
    from memory import db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "search.db")
    monkeypatch.setattr(db, "LEGACY_LONG_TERM", tmp_path / "missing.json")
    monkeypatch.setattr(db, "LEGACY_CONVERSATION", tmp_path / "missing2.json")
    first = db.start_conversation("first")
    db.append_conversation_turn("orchid project", "first result", conversation_id=first)
    second = db.start_conversation("second")
    db.append_conversation_turn("orchid project", "second result", conversation_id=second)
    rows = db.search_conversation_turns("orchid", conversation_id=first)
    assert [r["assistant"] for r in rows] == ["first result"]
    assert db.get_active_conversation()["id"] == second

def test_private_context_is_not_loaded(monkeypatch):
    from memory import conversation
    monkeypatch.setattr(conversation, "recent_messages", lambda **kwargs: (_ for _ in ()).throw(AssertionError("private history read")))
    assert conversation.build_turn_context("hello", share_cloud=False) == ([], "")


def test_explicit_local_recall_preserves_session_and_avoids_cloud(tmp_path, monkeypatch):
    from memory import db
    from core.agents import orchestrator as o
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "recall.db")
    monkeypatch.setattr(db, "LEGACY_LONG_TERM", tmp_path / "missing-long.json")
    monkeypatch.setattr(db, "LEGACY_CONVERSATION", tmp_path / "missing-history.json")
    first = db.start_conversation("first")
    db.append_conversation_turn("orchid project", "first answer", conversation_id=first)
    second = db.start_conversation("second")
    db.append_conversation_turn("orchid project", "private other answer", conversation_id=second)
    monkeypatch.setattr(o.llm, "load_config", lambda: (_ for _ in ()).throw(AssertionError("cloud accessed")))
    answer = o.MasterOrchestrator().execute("history search orchid", conversation_id=first,
        dispatch=lambda *args: None, declarations=lambda: [])
    assert "first answer" in answer
    assert "private other answer" not in answer
    assert db.get_active_conversation()["id"] == second


def test_schema_initialized_once_per_database(tmp_path, monkeypatch):
    from memory import db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "once.db")
    monkeypatch.setattr(db, "LEGACY_LONG_TERM", tmp_path / "missing-long.json")
    monkeypatch.setattr(db, "LEGACY_CONVERSATION", tmp_path / "missing-history.json")
    calls = []
    original = db._ensure_columns
    def counted(conn):
        calls.append(1)
        return original(conn)
    monkeypatch.setattr(db, "_ensure_columns", counted)
    db.init_db()
    db.append_conversation_turn("hello", "hi")
    db.load_conversation_turns()
    assert len(calls) == 1
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "other.db")
    db.init_db()
    assert len(calls) == 2


def test_natural_language_local_recall(tmp_path, monkeypatch):
    from memory import db, conversation
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "natural.db")
    monkeypatch.setattr(db, "LEGACY_LONG_TERM", tmp_path / "missing-long.json")
    monkeypatch.setattr(db, "LEGACY_CONVERSATION", tmp_path / "missing-history.json")
    cid = db.start_conversation("test")
    db.append_conversation_turn("orchid project", "saved answer", conversation_id=cid)
    assert "saved answer" in conversation.recall_local("What did we discuss about orchid?", conversation_id=cid)
    assert conversation.recall_local("Open orchid file", conversation_id=cid) is None


def test_preference_learning_and_atomic_updates(tmp_path, monkeypatch):
    from memory import db, memory_manager as mm
    from memory.learning import learn_explicit_preference
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "learning.db")
    monkeypatch.setattr(db, "LEGACY_LONG_TERM", tmp_path / "missing-long.json")
    monkeypatch.setattr(db, "LEGACY_CONVERSATION", tmp_path / "missing-history.json")
    db.save_session_summary("retain me")
    assert learn_explicit_preference("I prefer replies in English")
    assert learn_explicit_preference("I prefer replies in Hinglish")
    assert not learn_explicit_preference("I prefer using password=secret")
    mm.update_memory({"notes":{"topic":"MARK"}})
    result = db.load_memory_dict()
    assert result["preferences"]["reply_language"]["value"] == "hinglish"
    assert result["notes"]["topic"]["value"] == "MARK"
    assert result["sessions"][0]["summary"] == "retain me"
    mm.forget("topic")
    assert db.load_memory_dict()["preferences"]["reply_language"]["value"] == "hinglish"
