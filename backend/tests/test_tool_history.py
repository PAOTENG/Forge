from app.services.agent_runtime import _jsonable, close_unanswered_tool_calls


def test_closes_function_call_without_output():
    items = [
        {"role": "user", "content": "fix it"},
        {"type": "function_call", "name": "restart_service", "call_id": "c1", "arguments": "{}"},
    ]
    out = close_unanswered_tool_calls(items)
    assert out[-1]["type"] == "function_call_output"
    assert out[-1]["call_id"] == "c1"
    assert "not executed" in out[-1]["output"]


def test_keeps_answered_function_calls():
    items = [
        {"type": "function_call", "name": "bash", "call_id": "c1", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "c1", "output": "ok"},
    ]
    out = close_unanswered_tool_calls(items)
    assert out == items


def test_closes_chat_style_tool_calls():
    items = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "t1", "type": "function", "function": {"name": "bash", "arguments": "{}"}}],
        }
    ]
    out = close_unanswered_tool_calls(items)
    assert out[-1]["role"] == "tool"
    assert out[-1]["tool_call_id"] == "t1"


def test_jsonable_strips_postgres_nuls():
    items = [{"type": "function_call_output", "output": "pre\x00post"}]
    out = _jsonable(items)
    assert "\x00" not in out[0]["output"]
    assert out[0]["output"] == "prepost"
