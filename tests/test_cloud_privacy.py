from core.cloud_privacy import redact_messages

def test_redaction_does_not_mutate_history():
    original = [{"role": "user", "content": "password=example123"}]
    assert redact_messages(original, {})[0]["content"] == "password=[REDACTED]"
    assert original[0]["content"] == "password=example123"

def test_tool_correlation_preserved():
    messages = [{"role":"tool","tool_call_id":"abc","content":"Bearer secret-token"}]
    result = redact_messages(messages, {})
    assert result[0]["tool_call_id"] == "abc"
    assert result[0]["content"] == "Bearer [REDACTED]"
