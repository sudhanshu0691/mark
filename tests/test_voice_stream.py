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


def test_pcm_echo_is_suppressed_and_distinct_sustained_voice_interrupts():
    import numpy as np

    from core.voice_stream import PlaybackBargeIn

    rng = np.random.default_rng(12)
    audio = rng.integers(-2800, 2800, 2048, dtype=np.int16)
    gate = PlaybackBargeIn(speaker_rate=16_000)
    gate.playback(audio.tobytes())
    echo = (audio[400:1424] // 2).astype("<i2")
    assert gate.microphone(echo.tobytes(), playing=True) == (b"", False)
    speech = rng.integers(-3500, 3500, 1024, dtype=np.int16).tobytes()
    assert gate.microphone(speech, playing=True) == (b"", False)
    assert gate.microphone(speech, playing=True) == (speech * 2, True)
