"""Poll the knowledge-base agent over A2A. A failed canary becomes a duty alert."""

from __future__ import annotations

import asyncio
import logging
import time

from app.a2a.client import kb_skill
from app.core.config import get_settings
from app.services.duty import persist_alert

log = logging.getLogger("forge.a2a.kb")

_task: asyncio.Task | None = None
_last_fire = 0.0
_last_key = ""
ALERT_TITLE = "知识库问答 Agent 金丝雀失败 · A2A"


def _completed(task_dict: dict) -> bool:
    state = str((task_dict.get("status") or {}).get("state") or "")
    return state in {"TASK_STATE_COMPLETED", "COMPLETED", "completed"} or bool(task_dict.get("ok"))


async def tick() -> dict | None:
    global _last_fire, _last_key
    s = get_settings()
    if not s.kb_a2a_url:
        return None
    try:
        result = await kb_skill("kb-canary")
    except Exception as exc:
        result = {"ok": False, "error": str(exc), "status": {"state": "TASK_STATE_FAILED"}}
        log.warning("kb A2A canary unreachable: %s", exc)
    if _completed(result):
        return None
    key = str(result.get("error") or (result.get("status") or {}).get("state") or "failed")
    now = time.time()
    cooldown = max(30, int(s.forge_watchdog_cooldown))
    if key == _last_key and now - _last_fire < cooldown:
        return None
    _last_key = key
    _last_fire = now
    return await persist_alert(
        {
            "source": "a2a",
            "title": ALERT_TITLE,
            "summary": "Forge 经 A2A skill kb-canary 检查知识库问答 Agent，任务未完成。",
            "service": "kb-qa",
            "severity": "critical",
            "stack": str(result)[:8000],
            "link": s.kb_a2a_url.rstrip("/") + "/.well-known/agent-card.json",
        }
    )


async def loop() -> None:
    await asyncio.sleep(8)
    while True:
        try:
            await tick()
        except Exception as exc:
            log.warning("kb A2A watchdog tick failed: %s", exc)
        await asyncio.sleep(12)


def start_kb_watchdog() -> None:
    global _task
    if _task and not _task.done():
        return
    _task = asyncio.create_task(loop(), name="forge-kb-a2a-watchdog")


async def stop_kb_watchdog() -> None:
    global _task
    if _task:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
