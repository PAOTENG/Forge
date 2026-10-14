"""工具调用审计：SDK AgentHooks 在执行点落库，SHA-256 链防事后改写。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Any
from uuid import uuid4

from agents import AgentHooks
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import get_session_factory
from app.models.tool_call import ToolCallRow

log = logging.getLogger("forge.audit")

GENESIS = "0" * 64
_MAX = 12_000
_locks: dict[str, asyncio.Lock] = {}


def chain_hash(prev_hash: str, payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{prev_hash}\n{blob}".encode("utf-8")).hexdigest()


def _clip(value: object) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    if len(text) > _MAX:
        return text[:_MAX] + "\n…[truncated]"
    return text


def _lock_for(session_id: str) -> asyncio.Lock:
    lock = _locks.get(session_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[session_id] = lock
    return lock


def payload_for_hash(
    *,
    session_id: str,
    tool_name: str,
    tool_call_id: str,
    agent_name: str,
    arguments: str,
    output: str,
    actor: str,
    state: str,
    decision: str,
) -> dict[str, str]:
    return {
        "session_id": session_id,
        "tool_name": tool_name,
        "tool_call_id": tool_call_id,
        "agent_name": agent_name,
        "arguments": arguments,
        "output": output,
        "actor": actor,
        "state": state,
        "decision": decision,
    }


async def record_tool_event(
    *,
    session_id: str,
    tool_name: str,
    arguments: str = "",
    output: str = "",
    actor: str = "",
    state: str = "start",
    decision: str = "",
    agent_name: str = "",
    tool_call_id: str = "",
) -> ToolCallRow | None:
    if not session_id:
        return None
    args = _clip(arguments)
    out = _clip(output)
    body = payload_for_hash(
        session_id=session_id,
        tool_name=tool_name or "",
        tool_call_id=tool_call_id or "",
        agent_name=agent_name or "",
        arguments=args,
        output=out,
        actor=actor or "",
        state=state or "start",
        decision=decision or "",
    )
    async with _lock_for(session_id):
        factory = get_session_factory()
        try:
            async with factory() as db:
                prev = (
                    await db.execute(
                        select(ToolCallRow.hash)
                        .where(ToolCallRow.session_id == session_id)
                        .order_by(ToolCallRow.created_at.desc(), ToolCallRow.id.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                prev_hash = prev or GENESIS
                digest = chain_hash(prev_hash, body)
                row = ToolCallRow(
                    id=str(uuid4()),
                    session_id=session_id,
                    tool_name=body["tool_name"],
                    tool_call_id=body["tool_call_id"],
                    agent_name=body["agent_name"],
                    arguments=args,
                    output=out,
                    actor=body["actor"],
                    state=body["state"],
                    decision=body["decision"],
                    prev_hash=prev_hash,
                    hash=digest,
                )
                db.add(row)
                await db.commit()
                await db.refresh(row)
                return row
        except Exception:
            log.exception("tool audit persist failed session=%s tool=%s", session_id, tool_name)
            if get_settings().forge_audit_fail_closed:
                raise
            return None


def verify_chain(rows: list[ToolCallRow]) -> bool:
    prev = GENESIS
    for row in rows:
        body = payload_for_hash(
            session_id=row.session_id,
            tool_name=row.tool_name,
            tool_call_id=row.tool_call_id,
            agent_name=row.agent_name,
            arguments=row.arguments,
            output=row.output,
            actor=row.actor,
            state=row.state,
            decision=row.decision,
        )
        expected = chain_hash(prev, body)
        if row.prev_hash != prev or row.hash != expected:
            return False
        prev = row.hash
    return True


class ForgeToolAuditHooks(AgentHooks):
    """SDK 在本地 function-tool 执行前后回调；模型绕不过。"""

    async def on_tool_start(self, context, agent, tool) -> None:
        forge = getattr(context, "context", None)
        await record_tool_event(
            session_id=getattr(forge, "session_id", "") or "",
            tool_name=getattr(tool, "name", "") or getattr(context, "tool_name", "") or "",
            arguments=getattr(context, "tool_arguments", "") or "",
            actor=getattr(forge, "actor", "") or "",
            state="start",
            agent_name=getattr(agent, "name", "") or "",
            tool_call_id=getattr(context, "tool_call_id", "") or "",
        )

    async def on_tool_end(self, context, agent, tool, result: object) -> None:
        forge = getattr(context, "context", None)
        await record_tool_event(
            session_id=getattr(forge, "session_id", "") or "",
            tool_name=getattr(tool, "name", "") or getattr(context, "tool_name", "") or "",
            arguments=getattr(context, "tool_arguments", "") or "",
            output=result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str),
            actor=getattr(forge, "actor", "") or "",
            state="end",
            agent_name=getattr(agent, "name", "") or "",
            tool_call_id=getattr(context, "tool_call_id", "") or "",
        )
