import pytest
from core import llm

def test_offline_cloud_inference_rejected(monkeypatch):
    monkeypatch.setattr(llm, "load_config", lambda: {"profile": "offline", "backend": "multi_provider"})
    with pytest.raises(llm.LLMUnavailable):
        llm.chat_raw([{"role":"user","content":"hello"}])

def test_vision_uses_cloud_provider(monkeypatch):
    cfg = {**llm.DEFAULTS, "backend":"multi_provider", "allow_cloud_vision":True,
           "free_tier_only":False}
    monkeypatch.setattr(llm, "load_config", lambda: cfg)
    monkeypatch.setattr(llm, "_gemini_chat", lambda *args: {"content":"image"})
    assert llm.vision(b"image", "what is this?") == "image"
