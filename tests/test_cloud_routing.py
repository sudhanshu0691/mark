from core import llm


def test_unverified_free_model_stays_local(monkeypatch):
    cfg = {**llm.DEFAULTS, 'backend': 'multi_provider', 'free_tier_only': True, 'verified_free_models': []}
    monkeypatch.setattr(llm, 'load_config', lambda: cfg)
    monkeypatch.setattr(llm, '_ollama_chat', lambda *args: {'content': 'local'})
    monkeypatch.setattr(llm, '_groq_chat', lambda *args: (_ for _ in ()).throw(AssertionError('cloud called')))
    assert llm.chat_raw([{'role': 'user', 'content': 'hello'}])['content'] == 'local'


def test_cloud_omits_prior_history(monkeypatch):
    cfg = {**llm.DEFAULTS, 'backend': 'multi_provider', 'verified_free_models': ['test'], 'share_history_with_cloud': False}
    monkeypatch.setattr(llm, 'load_config', lambda: cfg)
    received = []
    monkeypatch.setattr(llm, '_groq_chat', lambda messages, *args: received.extend(messages) or {'content': 'ok'})
    llm.chat_raw([{'role': 'system', 'content': 'instructions'}, {'role': 'user', 'content': 'old'}, {'role': 'assistant', 'content': 'old reply'}, {'role': 'user', 'content': 'new'}])
    assert [m['content'] for m in received] == ['instructions', 'new']


def test_cloud_error_falls_back_locally(monkeypatch):
    cfg = {**llm.DEFAULTS, 'backend': 'multi_provider', 'verified_free_models': ['test']}
    monkeypatch.setattr(llm, 'load_config', lambda: cfg)
    monkeypatch.setattr(llm, '_groq_chat', lambda *args: (_ for _ in ()).throw(llm.LLMUnavailable('unavailable')))
    monkeypatch.setattr(llm, '_ollama_chat', lambda *args: {'content': 'local'})
    assert llm.chat_raw([{'role': 'user', 'content': 'hello'}])['content'] == 'local'
