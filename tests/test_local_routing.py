from core import llm


def test_local_chat_uses_configured_model_and_reports_activity(monkeypatch):
    cfg = {**llm.DEFAULTS, 'backend': 'ollama'}
    monkeypatch.setattr(llm, 'load_config', lambda: cfg)
    monkeypatch.setattr(llm, '_ensure_server', lambda: None)
    calls, events = [], []
    monkeypatch.setattr(llm, '_provider_listener', events.append)
    monkeypatch.setattr(llm, '_post', lambda path, payload: calls.append((path, payload)) or {'message': {'content': 'hello'}})
    assert llm.generate('hi') == 'hello'
    assert calls[0][0] == '/api/chat'
    assert calls[0][1]['model'] == cfg['text_model']
    assert events == ['ollama', 'sleep']


def test_local_vision_selects_vision_model(monkeypatch):
    cfg = {**llm.DEFAULTS, 'backend': 'ollama'}
    monkeypatch.setattr(llm, 'load_config', lambda: cfg)
    monkeypatch.setattr(llm, '_ensure_server', lambda: None)
    calls = []
    monkeypatch.setattr(llm, '_post', lambda path, payload: calls.append(payload) or {'message': {'content': 'image'}})
    assert llm.vision(b'image', 'what is this?') == 'image'
    assert calls[0]['model'] == cfg['vision_model']
    assert calls[0]['messages'][-1]['images']
