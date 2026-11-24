import asyncio
import pytest
from core import llm
from core._llm_gateway import CloudLLMGateway, TextDeltaEvent, LLMCompleteEvent

def test_stream_gateway_emits_deltas(monkeypatch):
    def chat(messages, **kwargs):
        kwargs["on_delta"]("hel")
        kwargs["on_delta"]("lo")
        return {"content": "hello"}
    monkeypatch.setattr(llm, "chat_raw", chat)
    async def collect():
        return [e async for e in CloudLLMGateway().generate("hi")]
    events = asyncio.run(collect())
    assert [e.delta for e in events if isinstance(e, TextDeltaEvent)] == ["hel", "lo"]
    assert events[-1] == LLMCompleteEvent("hello")

def test_no_fallback_after_partial_stream(monkeypatch):
    monkeypatch.setattr(llm, "load_config", lambda: {"backend":"multi_provider", "free_tier_only":False})
    def primary(*args):
        args[-2]("partial")
        raise llm.LLMUnavailable("interrupted")
    monkeypatch.setattr(llm, "_groq_chat", primary)
    monkeypatch.setattr(llm, "_gemini_chat", lambda *args: pytest.fail("must not duplicate partial answer"))
    received = []
    with pytest.raises(llm.LLMUnavailable):
        llm.chat_raw([{"role":"user","content":"hi"}], on_delta=received.append)
    assert received == ["partial"]
