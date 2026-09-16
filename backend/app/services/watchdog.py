"""
值班闭环的入口：盯着 order-api 的活金丝雀，红了进收件箱，绿了把旧告警关掉。

业务故事：
    旁边有个下单服务被故意埋了 bug。Forge API 启动后拉起该进程，本模块每 8 秒
    看一次 /canary。失败 → 插一条 DutyAlertRow → 企微/钉钉把人点进来 → 开会话修。
    修好并重启后金丝雀变绿 → 本模块把未关闭的 watchdog 告警标成 resolved。

本文件不跑 Agent、不改代码。Agent 从 duty.investigate / chat.send_message 才开始。

调用总图：
    main.lifespan → start_watchdog → loop → tick
                      tick 绿 → _resolve_open_watchdog
                      tick 红 → _has_open_watchdog / watchdog_key 去重
                              → DutyAlertRow + duty.notify_alert
    API 关闭 → stop_watchdog
"""

from __future__ import annotations

import asyncio
import logging
import time

from sqlalchemy import select

from app.services.supervisor import active_bug, canary_url, snapshot, target_name

log = logging.getLogger("forge.watchdog")

# 进程内存：上次告警时刻/状态键，避免 8 秒一轮把收件箱刷爆。
_last_fire = 0.0
_last_key = ""
_last_purge = 0.0
_task: asyncio.Task | None = None


def watchdog_key(name: str, bug: int, running: bool, canary: dict) -> str:
    """
    【业务】金丝雀可能连续很多轮都是 500。若每轮都插告警，收件箱会被同一题刷满。
            用「服务名 + 题号 + 进程是否在 + 状态码」收成一把钥匙，相同钥匙在冷却期内不重发。
    【是否主线】是主线的去重辅助，不直接面对用户。
    【输入】
        name：服务名，默认 order-api（target_name）,
        bug：active_bug.txt 里的题号，如 1。
        running：进程在不在。
        canary：snapshot()["canary"]，含 status_code / ok。
    【输出】
        进程挂了： "order-api:1:down"
        进程在但金丝雀红："order-api:1:500:False"
    【谁调用】tick() 在准备发告警前。
    【下一步】tick 拿这把钥匙和 _last_key 比，相同且未过 cooldown 就 return None。
    """
    if not running:
        return f"{name}:{bug}:down"
    return f"{name}:{bug}:{canary.get('status_code')}:{canary.get('ok')}"


async def _has_open_watchdog(service: str, bug: int) -> bool:
    """
    【业务】值班员已经在收件箱里看到「order-api 金丝雀失败 · BUG-1」，
            正在排查或已签收。这时金丝雀还是红的，不能再插第二条同一题。
    【是否主线】是。防止重复告警，不是可删的闲函数。
    【输入】
        service：服务名，如 order-api。
        bug：题号。用标题是否以 BUG-{n} 结尾来对题（watchdog 写标题时带这个后缀）。
    【输出】True=库里已有 source=watchdog 且状态为 open/investigating/acked 的同一题。
    【谁调用】tick() 在冷却判断之后、真正 insert 之前。
    【下一步】True → tick 只更新 _last_key/_last_fire，不写库、不推送；False → tick 继续组 body。
    """
    from app.db.session import get_session_factory
    from app.models.duty import DutyAlertRow

    factory = get_session_factory()
    marker = f"BUG-{bug}"
    async with factory() as db:
        rows = (
            await db.execute(
                select(DutyAlertRow).where(
                    DutyAlertRow.source == "watchdog",
                    DutyAlertRow.service == service,
                    DutyAlertRow.status.in_(("open", "investigating", "acked")),
                )
            )
        ).scalars()
        return any((row.title or "").rstrip().endswith(marker) for row in rows)


async def _resolve_open_watchdog(service: str, bug: int | None = None) -> None:
    """
    告警应自动关掉
    【业务】热修闭环的尾巴：模型改完、测试过、重启后 /canary 变 200。
            收件箱里这条 watchdog 告警应自动关掉，不用人再点「关闭」。
            bug 传 None 时关掉该服务下所有未关的 watchdog 告警（当前 tick 只这样用）。
    【是否主线】是。对应「修好了金丝雀复检」。
    【输入】
        service：服务名。
        bug：可选题号；None 表示该服务所有未关 watchdog 告警。
    【输出】无。副作用：匹配行的 status 改成 resolved 并 commit。
    【谁调用】tick() 发现 canary.ok 为真时。
    【下一步】没有。告警关了，本轮 tick 直接 return None。人若要再修下一题，靠落地页切题。
    """
    from app.db.session import get_session_factory
    from app.models.duty import DutyAlertRow

    factory = get_session_factory()
    marker = f"BUG-{bug}" if bug is not None else ""
    async with factory() as db:
        rows = (
            await db.execute(
                select(DutyAlertRow).where(
                    DutyAlertRow.source == "watchdog",
                    DutyAlertRow.service == service,
                    DutyAlertRow.status.in_(("open", "investigating", "acked")),
                )
            )
        ).scalars().all()
        changed = False
        for row in rows:
            if bug is None or (row.title or "").rstrip().endswith(marker):
                row.status = "resolved"
                changed = True
        if changed:
            await db.commit()


async def tick() -> dict | None:
    """
    【业务】「值班雷达扫一圈」。这是 watchdog 的核心：看活进程金丝雀，决定关告警还是拉人。
            不在这里开会话、不调模型。人点收件箱「排查」之后才进 Agent。
    【是否主线】是整条热修链的第一环。
    【输入】无。自己读配置、调 supervisor.snapshot / active_bug / target_name。
    【输出】
        None：关了 FORGE_WATCHDOG / 金丝雀绿 / 冷却中 / 该题已有未关告警。
        dict：刚插入的告警（含 id、title、prompt），给测试或日志看。
    【谁调用】loop() 每 8 秒一次；单测也可直接 await tick()。
    【下一步】
        绿 → _resolve_open_watchdog。
        红且该发 → normalize_inbound → 插入 DutyAlertRow
                 → format_investigation_prompt（预生成排查稿）
                 → duty.notify_alert（企微/钉钉）。
        人下一步：duty.investigate → start_prompt。
    """
    global _last_fire, _last_key
    from app.core.config import get_settings

    if not get_settings().forge_watchdog:
        return None
    state = await asyncio.to_thread(snapshot)
    canary = state["canary"]
    running = bool(state.get("running"))
    n = active_bug()
    name = target_name()
    if canary.get("ok"):
        await _resolve_open_watchdog(name)
        return None
    key = watchdog_key(name, n, running, canary)
    now = time.time()
    cooldown = max(30, int(get_settings().forge_watchdog_cooldown))
    if key == _last_key and now - _last_fire < cooldown:
        return None
    if await _has_open_watchdog(name, n):
        _last_key = key
        _last_fire = now
        return None
    _last_key = key
    _last_fire = now
    if not running:
        title = f"{name} 进程未在跑 · BUG-{n}"
        summary = f"值班机上看不到 {name} 进程。当前只激活 bug {n}。"
        stack = (state.get("health") or {}).get("body") or ""
        link = canary_url()
    else:
        title = f"{name} 金丝雀失败 · BUG-{n}"
        summary = f"{canary_url()} 返回 {canary.get('status_code')}。当前只激活 bug {n}，修这一个。"
        stack = canary.get("body") or ""
        link = canary_url()
    body = {
        "source": "watchdog",
        "title": title,
        "summary": summary,
        "service": name,
        "severity": "critical",
        "stack": stack,
        "link": link,
    }
    from app.db.session import get_session_factory
    from app.models.duty import DutyAlertRow
    from app.services.duty import format_investigation_prompt, normalize_inbound, notify_alert

    data = normalize_inbound(body)
    factory = get_session_factory()
    async with factory() as db:
        row = DutyAlertRow(**data)
        db.add(row)
        await db.commit()
        await db.refresh(row)
        public = {
            "id": row.id,
            "title": row.title,
            "summary": row.summary,
            "service": row.service,
            "severity": row.severity,
            "source": row.source,
            "stack": row.stack,
            "link": row.link,
            "status": row.status,
            "session_id": row.session_id,
            "prompt": format_investigation_prompt(data, state=state),
        }
    await notify_alert(public)
    log.warning("watchdog alert BUG-%s", n)
    return public


async def loop() -> None:
    """
    【业务】API 活着就一直巡检。先睡 4 秒等 order-api 拉起，避免启动瞬间误报「进程没了」。
            每小时顺带清过期写前备份——这不是热修主线，是挂在同一条后台任务上省一个定时器。
    【是否主线】循环本身是主线的节拍器；purge_expired 是顺带的运维。
    【输入】无。【输出】无（死循环，直到被 cancel）。
    【谁调用】start_watchdog() 里 asyncio.create_task(loop())。
    【下一步】每轮 await tick()；满 1 小时 → backup.purge_expired()；然后 sleep 8。
    """
    global _last_purge
    await asyncio.sleep(4)
    while True:
        try:
            await tick()
            now = time.time()
            if now - _last_purge > 3600:
                from app.services.backup import purge_expired

                await purge_expired()
                _last_purge = now
        except Exception as exc:
            log.warning("watchdog tick failed: %s", exc)
        await asyncio.sleep(8)


def start_watchdog() -> None:
    """
    【业务】Forge API 启动完成就打开雷达。不调用则金丝雀红了也不会进收件箱
            （仍可用「模拟告警」或 webhook 演示）。
    【是否主线】是进程级开关。
    【输入】无。【输出】无。已有未结束的 _task 则直接返回，防止 lifespan 重入起两条。
    【谁调用】backend/app/main.py 的 lifespan 启动阶段。
    【下一步】create_task(loop) → 4 秒后开始 tick。
    """
    global _task
    if _task and not _task.done():
        return
    _task = asyncio.create_task(loop(), name="forge-watchdog")


async def stop_watchdog() -> None:
    """
    【业务】关掉 API 时停雷达，避免进程都没了还在扫、或卸载后残留任务。
            热修演示关服务器时走这里，不是给人点的业务按钮。
    【是否主线】生命周期配套，关停用。
    【输入】无。【输出】无。
    【谁调用】main.py lifespan 的 shutdown（yield 之后）。
    【下一步】cancel loop 任务；没有再往下的业务调用。
    """
    global _task
    if _task:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
