from core.risk import validate_arguments


def test_nested_tool_arguments_validate_required_types_and_enum():
    declaration = {
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "slides": {
                    "type": "ARRAY",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "title": {"type": "STRING"},
                            "kind": {"type": "STRING", "enum": ["text"]},
                            "count": {"type": "INTEGER"},
                        },
                        "required": ["title"],
                    },
                }
            },
            "required": ["slides"],
        }
    }
    assert not validate_arguments(
        declaration, {"slides": [{"title": "Hello", "kind": "text"}]}
    )
    assert "slides[0].title" in validate_arguments(declaration, {"slides": [{}]})
    assert "slides[0].kind" in validate_arguments(
        declaration, {"slides": [{"title": "Hi", "kind": "other"}]}
    )
    assert "slides[0].count" in validate_arguments(
        declaration, {"slides": [{"title": "Hi", "count": True}]}
    )


def test_sensitive_facts_are_rejected_without_erasing_existing_memory(
    tmp_path, monkeypatch
):
    from memory import db, memory_manager

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "memory.db")
    monkeypatch.setattr(db, "LEGACY_LONG_TERM", tmp_path / "missing.json")
    monkeypatch.setattr(db, "LEGACY_CONVERSATION", tmp_path / "missing-history.json")
    assert db.upsert_fact("notes", "project", "MARK")
    assert not db.upsert_fact("notes", "api_key", "secret-value")
    assert not db.upsert_fact("notes", "note", "password = secret-value")
    assert memory_manager.remember("password", "secret-value").startswith("[FAILED]")
    import pytest

    with pytest.raises(ValueError):
        db.save_memory_dict({"notes": {"password": "secret-value"}})
    notes = db.load_memory_dict()["notes"]
    assert list(notes) == ["project"]
    assert notes["project"]["value"] == "MARK"
