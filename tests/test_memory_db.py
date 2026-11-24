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
