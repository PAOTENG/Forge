"""A2A client helpers: Forge talks to RoomCheck over official JSON-RPC."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from google.protobuf.json_format import MessageToDict

from a2a.client.client import ClientConfig
from a2a.client.client_factory import ClientFactory
from a2a.helpers.proto_helpers import new_text_message
from a2a.types.a2a_pb2 import Role, SendMessageRequest, TaskState

from app.a2a.pin import pin_or_verify
from app.a2a.token import bearer_header
from app.core.config import get_settings

log = logging.getLogger("forge.a2a.client")


def _headers(subject: str = "forge") -> dict[str, str]:
    return {"A2A-Version": "1.0", "Authorization": bearer_header(subject)}


def _tls_kwargs() -> dict:
    s = get_settings()
    kwargs: dict = {}
    if s.a2a_tls_ca:
        kwargs["verify"] = s.a2a_tls_ca
    cert = (s.a2a_tls_cert or "").strip()
    key = (s.a2a_tls_key or "").strip()
    if cert and key:
        kwargs["cert"] = (cert, key)
    elif cert:
        kwargs["cert"] = cert
    return kwargs


def _state_name(task_dict: dict) -> str:
    status = task_dict.get("status") or {}
    state = status.get("state") or ""
    return str(state)


async def send_skill(base_url: str, skill: str, payload: dict[str, Any] | None = None) -> dict:
    body = {"skill": skill, **(payload or {})}
    url = base_url.rstrip("/")
    async with httpx.AsyncClient(
        timeout=90.0, headers=_headers("forge"), trust_env=False, **_tls_kwargs()
    ) as http:
        card_url = url + "/.well-known/agent-card.json"
        card_res = await http.get(card_url)
        card_res.raise_for_status()
        pin_or_verify(url, card_res.content)
        factory = ClientFactory(ClientConfig(streaming=False, httpx_client=http))
        client = await factory.create_from_url(url)
        request = SendMessageRequest(
            message=new_text_message(json.dumps(body, ensure_ascii=False), role=Role.ROLE_USER)
        )
        task_dict: dict = {}
        async for event in client.send_message(request):
            if event.HasField("task"):
                task_dict = MessageToDict(event.task)
            elif event.HasField("status_update") and event.status_update.HasField("status"):
                task_dict.setdefault("status", {})
                task_dict["status"]["state"] = TaskState.Name(event.status_update.status.state)
        task_dict["ok"] = _state_name(task_dict) in {
            "TASK_STATE_COMPLETED",
            "COMPLETED",
            "completed",
        }
        return task_dict


async def roomcheck_skill(skill: str, payload: dict[str, Any] | None = None) -> dict:
    s = get_settings()
    url = (s.roomcheck_a2a_url or "").rstrip("/")
    if not url:
        raise RuntimeError("ROOMCHECK_A2A_URL is empty")
    return await send_skill(url, skill, payload)


async def kb_skill(skill: str, payload: dict[str, Any] | None = None) -> dict:
    s = get_settings()
    url = (s.kb_a2a_url or "").rstrip("/")
    if not url:
        raise RuntimeError("KB_A2A_URL is empty")
    return await send_skill(url, skill, payload)
