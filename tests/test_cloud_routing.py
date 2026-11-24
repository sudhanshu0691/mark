import pytest
from core import llm

def config(**overrides):
    return {**llm.DEFAULTS, "backend": "multi_provider", "primary_provider": "groq",
            "fallback_providers": ["gemini"], "automatic_fallback": True,
            "gemini": {"text_model": "test", "vision_model": "test"},
            "verified_free_models": ["test"], "share_history_with_cloud": False,
            **overrides}

def test_cloud_omits_prior_history(monkeypatch):
    monkeypatch.setattr(llm, "load_config", lambda: config())
    received = []
    monkeypatch.setattr(llm, "_groq_chat", lambda messages, *args: received.extend(messages) or {"content": "ok"})
    llm.chat_raw([{"role":"system","content":"instructions"},{"role":"user","content":"old"},
                  {"role":"assistant","content":"old reply"},{"role":"user","content":"new"}])
    assert [m["content"] for m in received] == ["instructions", "new"]

def test_cloud_fallback(monkeypatch):
    monkeypatch.setattr(llm, "load_config", lambda: config())
    def unavailable(*args):
        raise llm.LLMUnavailable("unavailable")
    monkeypatch.setattr(llm, "_groq_chat", unavailable)
    monkeypatch.setattr(llm, "_gemini_chat", lambda *args: {"content":"cloud fallback"})
    assert llm.chat_raw([{"role":"user","content":"hello"}])["content"] == "cloud fallback"

def test_all_providers_fail(monkeypatch):
    monkeypatch.setattr(llm, "load_config", lambda: config())
    def unavailable(*args):
        raise llm.LLMUnavailable("unavailable")
    monkeypatch.setattr(llm, "_groq_chat", unavailable)
    monkeypatch.setattr(llm, "_gemini_chat", unavailable)
    with pytest.raises(llm.LLMUnavailable, match="Cloud inference unavailable"):
        llm.chat_raw([{"role":"user","content":"hello"}])

def test_private_context_excluded(monkeypatch):
    monkeypatch.setattr(llm, "load_config", lambda: config(share_history_with_cloud=True))
    received = []
    monkeypatch.setattr(llm, "_groq_chat", lambda messages, *args: received.extend(messages) or {"content":"ok"})
    llm.chat_raw([{"role":"user","content":"private","_local_only":True},{"role":"user","content":"new"}])
    assert received == [{"role":"user","content":"new"}]

def test_tool_exchange_preserved(monkeypatch):
    monkeypatch.setattr(llm, "load_config", lambda: config())
    received = []
    monkeypatch.setattr(llm, "_groq_chat", lambda messages, *args: received.extend(messages) or {"content":"ok"})
    messages = [{"role":"user","content":"goal"},{"role":"assistant","content":"","tool_calls":[{"id":"abc","function":{"name":"check","arguments":"{}"}}]},
                {"role":"tool","tool_call_id":"abc","tool_name":"check","content":"result"}]
    llm.chat_raw(messages)
    assert received == messages
