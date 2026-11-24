import asyncio
from core import llm
from core.intelligence.intent import Intent, Complexity, model_role
from core.inference.gemini import GeminiProvider

def test_shared_roles():
    assert model_role(Intent.CODE, Complexity.HIGH, agentic=True) == "code"
    assert model_role(Intent.RESEARCH, Complexity.DEEP) == "planning"
    assert model_role(Intent.VISION, Complexity.MEDIUM, agentic=True) == "tool"
    assert model_role(Intent.VISION, Complexity.MEDIUM) == "vision"

def test_gemini_adapter_respects_gateway(monkeypatch):
    seen = []
    def fake(messages, **kwargs):
        seen.append(kwargs)
        return {"content": "ok"}
    monkeypatch.setattr(llm, "chat_raw", fake)
    provider = GeminiProvider()
    assert asyncio.run(provider.chat([{"role": "user", "content": "hello"}]))["content"] == "ok"
    assert seen[0]["provider_override"] == "gemini"
    assert not provider.get_capabilities()["supports_streaming"]

def test_provider_override_cannot_bypass_offline(monkeypatch):
    import pytest
    monkeypatch.setattr(llm, "load_config", lambda: {"profile": "offline"})
    with pytest.raises(llm.LLMUnavailable):
        llm.chat_raw([{"role": "user", "content": "hello"}], provider_override="gemini")
