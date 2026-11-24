"""Credential precedence and JSON persistence tests."""
import json
import sys
from types import SimpleNamespace

from core import secrets


def test_json_credential_is_primary_and_preserves_profile(tmp_path, monkeypatch):
    path = tmp_path / "api_keys.json"
    path.write_text(json.dumps({"gemini_api_key": "from-json", "user_name": "Ada"}), encoding="utf-8")
    monkeypatch.setattr(secrets, "LEGACY", path)
    monkeypatch.setenv("GEMINI_API_KEY", "from-env")
    assert secrets.get_secret("gemini") == "from-json"


def test_new_secret_uses_keyring_and_preserves_other_settings(tmp_path, monkeypatch):
    path = tmp_path / "api_keys.json"
    path.write_text(json.dumps({"user_name": "Ada", "mark_prefs": {"auto": True}}), encoding="utf-8")
    monkeypatch.setattr(secrets, "LEGACY", path)
    stored = {}
    fake_keyring = SimpleNamespace(set_password=lambda _, k, v: stored.update({k:v}), delete_password=lambda _, k: stored.pop(k))
    monkeypatch.setitem(sys.modules, "keyring", fake_keyring)
    secrets.set_secret("gemini", "test-key")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert stored["gemini"] == "test-key"
    assert "gemini_api_key" not in data
    assert data["user_name"] == "Ada"
    secrets.delete_secret("gemini")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "gemini_api_key" not in data
    assert data["mark_prefs"] == {"auto": True}
