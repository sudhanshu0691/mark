"""Gemini provider behavior with mocked SDK calls; no API charges."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import llm


@pytest.fixture
def gemini_config(tmp_path, monkeypatch):
    path = tmp_path / "llm.json"
    path.write_text(json.dumps({"backend": "gemini", "free_tier_only": False, "gemini": {
        "text_model": "gemini-3.8-flash", "live_model": "gemini-3.8-live",
        "memory_model": "gemini-3.8-flash", "timeout_s": 2, "max_retries": 2,
        "retry_backoff_s": [0, 0], "health_cache_s": 30}}), encoding="utf-8")
    monkeypatch.setattr(llm, "CONFIG_PATH", path)
    monkeypatch.setattr(llm, "_cfg_cache", None)
    monkeypatch.delenv("MARK_LLM_BACKEND", raising=False)
    monkeypatch.setattr("core.secrets.get_secret", lambda _: "test-key")
    monkeypatch.setattr(llm.time, "sleep", lambda _: None)
    monkeypatch.setattr(llm.random, "uniform", lambda *_: 0)
    llm._health_cache.update(at=0, value=None)
    yield
    llm._cfg_cache = None
    llm._health_cache.update(at=0, value=None)


def install_sdk(monkeypatch, generate, list_models=None):
    class Models:
        def generate_content(self, **kwargs):
            return generate(**kwargs)
        def list(self):
            return list_models or []
    class Client:
        def __init__(self, **kwargs):
            self.models = Models()
    monkeypatch.setattr("google.genai.Client", Client)


def test_text_success_uses_central_model(gemini_config, monkeypatch):
    seen = []
    install_sdk(monkeypatch, lambda **kw: (seen.append(kw) or SimpleNamespace(text="hello")))
    assert llm._gemini_generate("text", "hi", {}).text == "hello"
    assert seen[0]["model"] == "gemini-3.8-flash"


@pytest.mark.parametrize(("error", "message"), [
    (RuntimeError("HTTP 401 invalid key"), "HTTP 401"),
    (RuntimeError("HTTP 403 forbidden"), "HTTP 403"),
    (RuntimeError("HTTP 404 model not found"), "HTTP 404"),
])
def test_permanent_errors_are_not_retried(gemini_config, monkeypatch, error, message):
    calls = []
    install_sdk(monkeypatch, lambda **_: (calls.append(1) or (_ for _ in ()).throw(error)))
    with pytest.raises(llm.LLMUnavailable, match=message):
        llm.generate("hi")
    assert len(calls) == 1


@pytest.mark.parametrize("status", [429, 503])
def test_transient_errors_have_bounded_retries(gemini_config, monkeypatch, status):
    calls = []
    def generate(**_):
        calls.append(1)
        raise RuntimeError(f"HTTP {status} unavailable")
    install_sdk(monkeypatch, generate)
    with pytest.raises(llm.LLMUnavailable):
        llm.generate("hi")
    assert len(calls) == 3


def test_empty_response_rejected(gemini_config, monkeypatch):
    install_sdk(monkeypatch, lambda **_: SimpleNamespace(text=""))
    with pytest.raises(llm.LLMUnavailable, match="empty"):
        llm.generate("hi")


def test_health_reflects_text_and_memory_generation(gemini_config, monkeypatch):
    calls = []
    install_sdk(monkeypatch, lambda **kw: (calls.append(kw["model"]) or SimpleNamespace(text="OK")))
    monkeypatch.setattr(llm, "_gemini_generate_model", lambda model, *_: SimpleNamespace(text="OK"))
    class Session:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return False
    class Live:
        def connect(self, **_): return Session()
    class Client:
        def __init__(self, **_):
            self.models = SimpleNamespace(generate_content=lambda **_: SimpleNamespace(text="OK"))
            self.aio = SimpleNamespace(live=Live())
    monkeypatch.setattr("google.genai.Client", Client)
    result = llm.gemini_health(refresh=True)
    assert result["text"]["ready"] and result["memory"]["ready"]
    assert result["text"]["model"] == "gemini-3.8-flash"
