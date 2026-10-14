"""Official A2A server for Forge: ingest-incident → duty inbox."""

from __future__ import annotations

import json
import logging
from uuid import uuid4

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from a2a.helpers.proto_helpers import new_data_part, new_task, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.routes.fastapi_routes import add_a2a_routes_to_fastapi
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    HTTPAuthSecurityScheme,
    SecurityScheme,
    TaskState,
)
from a2a.utils.constants import PROTOCOL_VERSION_1_0, TransportProtocol

from app.a2a.token import verify_a2a_authorization
from app.core.config import get_settings

log = logging.getLogger("forge.a2a")


def parse_skill_payload(text: str) -> dict:
    raw = (text or "").strip()
    if raw[:1] in "{[":
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                data.setdefault("skill", "ingest-incident")
                return data
        except json.JSONDecodeError:
            pass
    return {"skill": "ingest-incident", "summary": raw, "title": "A2A incident"}


def build_agent_card() -> AgentCard:
    s = get_settings()
    url = f"http://{s.forge_api_host}:{s.forge_api_port}"
    card = AgentCard(
        name="Forge",
        description="Duty coding agent. Accepts software incidents from peer agents over A2A.",
        version="1.0.0",
        default_input_modes=["text/plain", "application/json"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=False, push_notifications=False),
    )
    card.supported_interfaces.append(
        AgentInterface(
            url=url,
            protocol_binding=TransportProtocol.JSONRPC,
            protocol_version=PROTOCOL_VERSION_1_0,
        )
    )
    card.skills.append(
        AgentSkill(
            id="ingest-incident",
            name="接入软件故障",
            description="将对端 Agent 的软件故障写入值班收件箱。业务不合格（缺茶杯）不要走这条。",
            tags=["duty", "incident"],
            examples=['{"skill":"ingest-incident","title":"roomcheck 金丝雀失败 · BUG-3"}'],
            input_modes=["text/plain", "application/json"],
            output_modes=["application/json"],
        )
    )
    card.security_schemes["bearer"].CopyFrom(
        SecurityScheme(http_auth_security_scheme=HTTPAuthSecurityScheme(scheme="Bearer"))
    )
    req = card.security_requirements.add()
    req.schemes["bearer"]
    return card


class ForgeExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id or str(uuid4())
        context_id = context.context_id or ""
        await event_queue.enqueue_event(new_task(task_id, context_id, TaskState.TASK_STATE_SUBMITTED))
        updater = TaskUpdater(event_queue, task_id, context_id)
        await updater.start_work()
        payload = parse_skill_payload(context.get_user_input())
        skill = str(payload.get("skill") or "ingest-incident")
        log.info("a2a task %s skill=%s", updater.task_id, skill)
        try:
            if skill not in {"ingest-incident", "incident", "escalate"}:
                raise ValueError(f"unknown skill {skill}")
            from app.services.duty import persist_alert

            alert = await persist_alert(
                {
                    "source": "a2a",
                    "title": str(payload.get("title") or "A2A incident")[:240],
                    "summary": str(payload.get("summary") or payload.get("text") or "")[:8000],
                    "service": str(payload.get("service") or "roomcheck")[:120],
                    "severity": str(payload.get("severity") or "critical")[:32],
                    "stack": str(payload.get("stack") or "")[:12000],
                    "link": str(payload.get("link") or "")[:2000],
                }
            )
            result = {"ok": True, "alert": alert, "taskId": updater.task_id}
            await updater.add_artifact(
                parts=[new_data_part(result, media_type="application/json")],
                name="ingest-incident",
                last_chunk=True,
            )
            await updater.complete(
                updater.new_agent_message([new_text_part(json.dumps({"ok": True, "id": alert.get("id")}, ensure_ascii=False))])
            )
        except Exception as exc:
            log.exception("forge a2a execute failed")
            await updater.add_artifact(
                parts=[new_data_part({"ok": False, "error": str(exc)}, media_type="application/json")],
                name="error",
                last_chunk=True,
            )
            await updater.failed(updater.new_agent_message([new_text_part(str(exc))]))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        updater = TaskUpdater(event_queue, context.task_id or "", context.context_id or "")
        await updater.cancel()


class _BearerGate(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method == "POST" and request.url.path in {"/", ""}:
            got = request.headers.get("authorization") or ""
            if not verify_a2a_authorization(got):
                return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


def mount_a2a(app: FastAPI) -> None:
    card = build_agent_card()
    handler = DefaultRequestHandler(
        agent_executor=ForgeExecutor(),
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(card),
        jsonrpc_routes=create_jsonrpc_routes(handler, "/"),
    )
    app.add_middleware(_BearerGate)
    app.state.a2a_handler = handler
