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


def test_structured_credentials_redacted_without_changing_ids():
    import json
    message = {"role": "assistant", "tool_calls": [{"id": "abc", "function": {"name": "connect", "arguments": json.dumps({"password": "example123", "host": "localhost", "nested": {"api_key": "private-value"}})}}]}
    result = redact_messages([message], {})[0]["tool_calls"][0]
    assert result["id"] == "abc"
    assert result["function"]["name"] == "connect"
    assert json.loads(result["function"]["arguments"]) == {"password": "[REDACTED]", "host": "localhost", "nested": {"api_key": "[REDACTED]"}}
    assert "example123" in message["tool_calls"][0]["function"]["arguments"]


def test_native_gemini_credentials_redacted():
    from google.genai import types
    content = types.Content(role="model", parts=[types.Part.from_function_call(name="connect", args={"password": "example123", "host": "local"})])
    result = redact_messages([{"role": "assistant", "_gemini_content": content}], {})[0]["_gemini_content"]
    assert result.parts[0].function_call.args == {"password": "[REDACTED]", "host": "local"}
    assert content.parts[0].function_call.args["password"] == "example123"
