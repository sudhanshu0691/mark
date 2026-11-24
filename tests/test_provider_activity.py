from core import llm


def test_provider_status_returns_to_sleep_after_success_and_failure(monkeypatch):
    events = []
    monkeypatch.setattr(llm, '_provider_listener', events.append)
    monkeypatch.setattr(llm, 'load_config', lambda: {'backend': 'ollama'})
    monkeypatch.setattr(llm, '_ollama_chat', lambda *args: {'content': 'done'})
    assert llm.chat_raw([{'role': 'user', 'content': 'hello'}])['content'] == 'done'
    assert events == ['ollama', 'sleep']
    events.clear()
    def fail(*args):
        raise llm.LLMUnavailable('offline')
    monkeypatch.setattr(llm, '_ollama_chat', fail)
    try:
        llm.chat_raw([{'role': 'user', 'content': 'hello'}])
    except llm.LLMUnavailable:
        pass
    assert events == ['ollama', 'sleep']

