from core.voice_stream import SentenceBuffer
from core.agents import orchestrator as o

def test_sentence_buffer():
    b = SentenceBuffer()
    assert b.feed("Hello wor") == []
    assert b.feed("ld! Next") == ["Hello world!"]
    assert b.flush() == "Next"

def test_conversation_delta_forwarded(monkeypatch):
    monkeypatch.setattr(o, "is_conversation", lambda _: True)
    monkeypatch.setattr(o, "match_local_command", lambda _: None)
    monkeypatch.setattr(o, "build_turn_context", lambda *a, **k: ([], ""))
    monkeypatch.setattr(o.llm, "load_config", lambda: {})
    def chat(messages, **kwargs):
        kwargs["on_delta"]("Hello.")
        return {"content": "Hello."}
    monkeypatch.setattr(o.llm, "chat_raw", chat)
    seen = []
    answer = o.MasterOrchestrator().execute("hi", dispatch=lambda *a: None, declarations=lambda: [], on_delta=seen.append)
    assert answer == "Hello."
    assert seen == ["Hello."]
