from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from google.protobuf.json_format import MessageToDict

from a2a.helpers.proto_helpers import new_text_message
from a2a.types.a2a_pb2 import Role, SendMessageRequest

from app.a2a.server import mount_a2a
from app.a2a.token import issue_a2a_token
from app.tools import all_tools


def _tool_name(tool) -> str:
    return str(getattr(tool, "name", "") or "")


def _rpc_client() -> TestClient:
    app = FastAPI()
    mount_a2a(app)
    return TestClient(app)


def _task_from_rpc(res) -> dict:
    body = res.json()
    assert body.get("error") in (None, {}), body
    result = body.get("result") or {}
    return result.get("task") or result


def _send_message_body(text: str) -> dict:
    req = SendMessageRequest(message=new_text_message(text, role=Role.ROLE_USER))
    return {
        "jsonrpc": "2.0",
        "id": "1",
        "method": "SendMessage",
        "params": MessageToDict(req),
    }


def test_forge_agent_card():
    client = _rpc_client()
    res = client.get("/.well-known/agent-card.json")
    assert res.status_code == 200
    card = res.json()
    skills = {s["id"] for s in card.get("skills") or []}
    assert "ingest-incident" in skills


def test_forge_jsonrpc_requires_bearer():
    client = _rpc_client()
    res = client.post("/", json=_send_message_body('{"skill":"ingest-incident","title":"x"}'))
    assert res.status_code == 401


def test_ingest_incident_creates_a2a_source_alert():
    client = _rpc_client()
    fake = {
        "id": "alert-1",
        "source": "a2a",
        "title": "roomcheck 检查失败 · BUG-3",
        "status": "open",
    }
    with patch("app.services.duty.persist_alert", new=AsyncMock(return_value=fake)):
        res = client.post(
            "/",
            json=_send_message_body(
                '{"skill":"ingest-incident","title":"roomcheck 检查失败 · BUG-3","summary":"boom"}'
            ),
            headers={"Authorization": f"Bearer {issue_a2a_token()}", "A2A-Version": "1.0"},
        )
    assert res.status_code == 200, res.text
    task = _task_from_rpc(res)
    state = str((task.get("status") or {}).get("state") or "")
    assert "COMPLETED" in state.upper()
    assert task.get("id")


def test_a2a_roomcheck_is_a_tool():
    names = {_tool_name(t) for t in all_tools()}
    assert "a2a_roomcheck" in names
    assert "a2a_kb" in names
