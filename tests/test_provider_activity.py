from core import llm


def test_provider_status_returns_to_sleep_after_success_and_failure(monkeypatch):
    events = []
    monkeypatch.setattr(llm, '_provider_listener', events.append)
    monkeypatch.setattr(llm, 'load_config', lambda: {'backend': 'multi_provider', 'automatic_fallback': False})
    monkeypatch.setattr(llm, '_groq_chat', lambda *args: {'content': 'done'})
    assert llm.chat_raw([{'role': 'user', 'content': 'hello'}])['content'] == 'done'
    assert events == ['groq', 'sleep']
    events.clear()
    def fail(*args):
        raise llm.LLMUnavailable('offline')
    monkeypatch.setattr(llm, '_groq_chat', fail)
    try:
        llm.chat_raw([{'role': 'user', 'content': 'hello'}])
    except llm.LLMUnavailable:
        pass
    assert events == ['groq', 'sleep']

