"""Poll RoomCheck over A2A canary. order-api HTTP watchdog stays separate."""

from __future__ import annotations

import asyncio
import logging
import time

from app.a2a.client import roomcheck_skill
from app.core.config import get_settings
from app.services.duty import persist_alert

log = logging.getLogger("forge.a2a.watchdog")

_task: asyncio.Task | None = None
_last_fire = 0.0
_last_key = ""


def _completed(task_dict: dict) -> bool:
    state = str((task_dict.get("status") or {}).get("state") or "")
    return state in {"TASK_STATE_COMPLETED", "COMPLETED", "completed"} or bool(task_dict.get("ok"))


async def tick() -> dict | None:
    global _last_fire, _last_key
    s = get_settings()
    if not s.roomcheck_a2a_url:
        return None
    try:
        result = await roomcheck_skill("room-canary")
    except Exception as exc:
        result = {"ok": False, "error": str(exc), "status": {"state": "TASK_STATE_FAILED"}}
        log.warning("roomcheck A2A canary unreachable: %s", exc)
    if _completed(result):
        return None
    key = str(result.get("error") or (result.get("status") or {}).get("state") or "failed")
    now = time.time()
    cooldown = max(30, int(s.forge_watchdog_cooldown))
    if key == _last_key and now - _last_fire < cooldown:
        return None
    _last_key = key
    _last_fire = now
    title = "roomcheck 金丝雀失败 · A2A"
    summary = "Forge 经 A2A skill room-canary 复检未 completed。缺茶杯不算这条。"
    stack = str(result)[:8000]
    return await persist_alert(
        {
            "source": "a2a",
            "title": title,
            "summary": summary,
            "service": "roomcheck",
            "severity": "critical",
            "stack": stack,
            "link": s.roomcheck_a2a_url.rstrip("/") + "/.well-known/agent-card.json",
        }
    )


async def loop() -> None:
    await asyncio.sleep(6)
    while True:
        try:
            await tick()
        except Exception as exc:
            log.warning("roomcheck A2A watchdog tick failed: %s", exc)
        await asyncio.sleep(12)


def start_roomcheck_watchdog() -> None:
    global _task
    if _task and not _task.done():
        return
    _task = asyncio.create_task(loop(), name="forge-roomcheck-a2a-watchdog")


async def stop_roomcheck_watchdog() -> None:
    global _task
    if _task:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
