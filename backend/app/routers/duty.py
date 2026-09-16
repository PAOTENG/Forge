"""
值班 HTTP 入口：收件箱、排查开会话、外部告警、手机批准卡、小商店启停切题。

业务故事：
    watchdog 把金丝雀红灯写成 DutyAlertRow 之后，人在本文件提供的接口上办事：
    看列表 → 点排查（investigate，不开模型）→ 前端再去 chat.send_message。
    人不在工位时，钉钉卡打开 /card，点允许走 /card-decide 或 POST /decide。

本文件不跑 Agent。开会话在 investigate；跑模型在 chat / agent_runtime。

调用总图：
    列表/详情 → list_alerts / get_alert
    点排查 → investigate → 前端 start_prompt
    外部推送 → require_duty_ingest → ingest_alert
    演示 → demo_alert
    HITL 卡 → approval_card → card_decide / decide_from_card → resume_after_approval
    落地页 → target_status / start / restart / next_bug
"""

from __future__ import annotations

import asyncio
import html
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import CurrentUser, require_engineer, require_token
from app.db.session import get_db, get_session_factory
from app.models.duty import DutyAlertRow
from app.models.session import SessionRow
from app.schemas import SessionOut
from app.services.agent_runtime import model_label, resume_after_approval
from app.models.agent_state import AgentStateRow
from app.services.duty import (
    channel_status,
    consume_pending,
    format_investigation_prompt,
    h5_url,
    lookup_pending,
    normalize_inbound,
    notify_alert,
    notify_approval,
)
from app.services.messages import add_message
from app.services.sandbox import SandboxError, sandbox_manager
from app.services.workspace import WorkspaceError, resolve_workspace

# 所有本文件接口的公共前缀：完整地址是 /api + /duty + 具体路径。
router = APIRouter(prefix="/duty", tags=["duty"])
# 日志记录器名字，不是磁盘文件名；默认打到 uvicorn 终端。
log = logging.getLogger("forge.duty")


class DutyAlertIn(BaseModel):
    """
    【业务】外面监控 POST /api/duty/alerts 时，请求 JSON 的验收单。
            不是收件箱里已存好的那一行（那是 DutyAlertRow）。
    【技术】Pydantic 模型。extra=allow：多来的键不拒，留给 normalize_inbound。
            title/summary/stack/link 是正名；alertname/message/text/log/url 是别名；
            alerts 非空则按 Alertmanager 包解析。
    【是否主线】旁路。雷达 tick() 自己拼 dict，不走这个类。
    """
    model_config = ConfigDict(extra="allow")

    source: str = "webhook"
    title: str = ""
    summary: str = ""
    service: str = ""
    severity: str = "warning"
    stack: str = ""
    link: str = ""
    alerts: list | None = None
    message: str = ""
    text: str = ""
    log: str = ""
    url: str = ""
    alertname: str = ""


class DutyDecideIn(BaseModel):
    """
    【业务】人在值班通道点「允许/拒绝」时的身体。approved=是否执行这次工具；
            scope=once 只批一次，session 本会话后续少打断。
    【技术】给 POST /decide 用。工作台同类是 schemas.ApprovalIn。
    【是否主线】HITL 旁路，点排查用不到。
    """
    approved: bool
    scope: str = "once"


def _public(row: DutyAlertRow) -> dict:
    """
    【业务】把库里一张告警收成前端收件箱卡片要的那一包，并附带排查稿。
    【技术】ORM 行 → dict；时间转 isoformat；prompt 现算不落库。
    【是否主线】展示帮手。列表/详情/签收/关闭都复用，避免每个接口手写字段。
    【输入】DutyAlertRow。【输出】含 id/title/status/prompt 等的 dict。
    【谁调用】list_alerts、get_alert、ingest/demo、resolve/ack/assign。
    """
    return {
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
        "assigned_to": row.assigned_to,
        "acked_by": row.acked_by,
        "acked_at": row.acked_at.isoformat() if row.acked_at else "",
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "prompt": format_investigation_prompt(
            {
                "title": row.title,
                "summary": row.summary,
                "service": row.service,
                "severity": row.severity,
                "stack": row.stack,
                "link": row.link,
            }
        ),
    }


async def require_duty_ingest(
    x_forge_duty_token: str | None = Header(default=None, alias="X-Forge-Duty-Token"),
    token: str | None = Query(default=None),
) -> None:
    """
    【业务】外部监控和手机卡片不是网页登录用户，用口令进门。
            头 X-Forge-Duty-Token 或 ?token= 对上才放行。两个配置都空则免检（本机演示）。
    【技术】FastAPI 依赖，返回 None。口令优先 FORGE_DUTY_TOKEN，否则 FORGE_API_TOKEN。
    【是否主线】否。investigate 走 require_engineer。
    【谁调用】ingest_alert、decide_from_card 的 Depends。
    """
    settings = get_settings()
    expected = (settings.forge_duty_token or settings.forge_api_token).strip()
    if not expected:
        return
    got = (x_forge_duty_token or token or "").strip()
    if got != expected:
        raise HTTPException(401, "Invalid duty token")


@router.get("/status")
async def duty_status(_auth: None = Depends(require_token)) -> dict:
    """
    【业务】落地页问：钉钉/企微配了没、值班 URL、工作区挂哪。
    【技术】GET /api/duty/status。要登录。db 不用，只读配置。
    【是否主线】落地页，不是热修内核。
    """
    return {"ok": True, **channel_status()}


@router.get("/alerts")
async def list_alerts(
    db: AsyncSession = Depends(get_db),
    _auth: None = Depends(require_token),
    include_resolved: bool = Query(False),
) -> dict:
    """
    【业务】打开收件箱：最近 40 张纸条，新的在前。默认不看已关闭。
    【技术】GET /api/duty/alerts。Depends(get_db) 注入这一次库会话。
    【是否主线】是。人看见 watchdog 插进来的 BUG-5 就靠它。
    【下一步】点一张 → get_alert；点排查 → investigate。
    """
    stmt = select(DutyAlertRow).order_by(DutyAlertRow.created_at.desc()).limit(40)
    if not include_resolved:
        stmt = stmt.where(DutyAlertRow.status != "resolved")
    rows = (await db.execute(stmt)).scalars()
    return {"alerts": [_public(r) for r in rows]}


@router.get("/alerts/{alert_id}")
async def get_alert(
    alert_id: str, db: AsyncSession = Depends(get_db), _auth: None = Depends(require_token)
) -> dict:
    """
    【业务】看这一张告警的详情（含排查稿）。
    【技术】按主键 get；没有则 404。钉钉链接带 alert= 时前端会打这里。
    【是否主线】列表的下一跳，可扫一眼。
    """
    row = await db.get(DutyAlertRow, alert_id)
    if not row:
        raise HTTPException(404, "告警不存在")
    return _public(row)


@router.post("/alerts")
async def ingest_alert(
    body: DutyAlertIn,
    db: AsyncSession = Depends(get_db),
    _auth: None = Depends(require_duty_ingest),
) -> dict:
    """
    【业务】Prometheus / 脚本把告警推进收件箱，并推钉钉叫人。
    【技术】口令门卫 → normalize_inbound 收成表字段 → insert → 后台 notify_alert。
            不走 watchdog 去重。推送 create_task，避免挡住对方的 webhook。
    【是否主线】旁路。热修演示主要靠雷达或 demo。
    """
    data = normalize_inbound(body.model_dump())
    row = DutyAlertRow(**data)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    public = _public(row)
    asyncio.create_task(notify_alert(public))
    return {"ok": True, "alert": public}


@router.post("/alerts/demo")
async def demo_alert(
    db: AsyncSession = Depends(get_db), _user: CurrentUser = Depends(require_engineer)
) -> dict:
    """
    【业务】落地页「模拟告警」：插一条写死的下单 500，source=demo。
            没配监控也能走「收件箱 → 排查」演示。
    【技术】要工程师登录。其余与 ingest 相同（normalize + 插行 + 后台推送）。
    【是否主线】演示旁路。标题没有 · BUG-n，watchdog 去重不认它。
    """
    data = normalize_inbound(
        {
            "source": "demo",
            "title": "下单接口 500",
            "summary": "order-api /v1/orders 出现 TypeError: NoneType has no attribute reserve",
            "service": "order-api",
            "severity": "critical",
            "stack": (
                "Traceback (most recent call last):\n"
                '  File "app/orders.py", line 88, in create_order\n'
                "    stock.reserve()\n"
                "TypeError: 'NoneType' object has no attribute 'reserve'"
            ),
        }
    )
    row = DutyAlertRow(**data)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    public = _public(row)
    asyncio.create_task(notify_alert(public))
    return {"ok": True, "alert": public}


def _prompt_from_row(row: DutyAlertRow) -> str:
    """
    【业务】从这张纸条生成给模型的第一段工作说明（只修当前题、先测后重启）。
    【技术】抽字段交给 format_investigation_prompt。本函数不打金丝雀，
            服务层里若没传 snapshot 会自己取（3 秒缓存）。
    【是否主线】是。investigate 返回的 prompt 走这里。
    """
    return format_investigation_prompt(
        {
            "title": row.title,
            "summary": row.summary,
            "service": row.service,
            "severity": row.severity,
            "stack": row.stack,
            "link": row.link,
        }
    )


def _session_out(session: SessionRow) -> SessionOut:
    """
    【业务】给前端跳进工作台用的会话身份证：id、标题、路径、模型、模式。
    【技术】表行 → SessionOut schema，不把整张行（含内部字段）扔给浏览器。
    【谁调用】investigate 新建或恢复时。
    """
    return SessionOut(
        id=session.id,
        title=session.title,
        workspace_path=session.workspace_path,
        model=session.model,
        mode=session.mode,
    )


@router.post("/alerts/{alert_id}/investigate")
async def investigate(
    alert_id: str,
    db: AsyncSession = Depends(get_db),
    _user: CurrentUser = Depends(require_engineer),
) -> dict:
    """
    【业务】收件箱点「排查」：为这张纸条开（或恢复）一个挂在 order-api 上的工作台会话，
            状态改 investigating，把排查稿返回给前端。这里不跑模型。
            已开过会则 resumed=true，不新建第二条会话。
    【技术】POST /api/duty/alerts/{id}/investigate。
            工作区 = FORGE_DUTY_WORKSPACE 或 target_root()；mode 固定 build（才能写文件）。
            flush 后才有 session.id 可写回告警。
            沙箱：这里第一次 ensure（热修主线启动笼子的时机）；
            已排透过则对已有 session 再 ensure 一次（刷新后容器可能没了）。
            索引 create_task 以免卡跳转。
    【是否主线】是热修链第二环（watchdog.tick 之后）。
    【输出】{ok, resumed, prompt, session}。前端若未 resumed 会 dispatchPrompt → chat.send_message。
    【下一步】chat.send_message → start_prompt。
    """
    row = await db.get(DutyAlertRow, alert_id)
    if not row:
        raise HTTPException(404, "告警不存在")
    if row.session_id:
        existing = await db.get(SessionRow, row.session_id)
        if existing:
            try:
                sandbox_manager.ensure(existing.id, resolve_workspace(existing.workspace_path))
            except (SandboxError, WorkspaceError) as exc:
                raise HTTPException(500, str(exc)) from exc
            return {
                "ok": True,
                "resumed": True,
                "prompt": _prompt_from_row(row),
                "session": _session_out(existing),
            }
    settings = get_settings()
    from app.services.supervisor import target_root

    workspace_path = settings.forge_duty_workspace.strip() or str(target_root())
    try:
        workspace = resolve_workspace(workspace_path)
    except WorkspaceError as exc:
        raise HTTPException(400, str(exc)) from exc
    session = SessionRow(
        title=f"值班 · {row.title}"[:200],
        workspace_path=str(workspace),
        model=model_label(),
        mode="build",
    )
    db.add(session)
    await db.flush()
    row.session_id = session.id
    row.status = "investigating"
    await db.commit()
    await db.refresh(session)
    try:
        sandbox_manager.ensure(session.id, workspace)
    except SandboxError as exc:
        raise HTTPException(500, str(exc)) from exc

    async def _index() -> None:
        # 请求已返回后才切块，search_code 才有数据。失败只 rollback，不影响排查。
        from app.services.indexer import index_workspace

        factory = get_session_factory()
        async with factory() as extra:
            try:
                await index_workspace(extra, workspace)
            except Exception:
                await extra.rollback()

    asyncio.create_task(_index())
    return {
        "ok": True,
        "resumed": False,
        "prompt": _prompt_from_row(row),
        "session": _session_out(session),
    }


@router.post("/decide")
async def decide_from_card(
    body: DutyDecideIn,
    token: str = Query(...),
    db: AsyncSession = Depends(get_db),
    _auth: None = Depends(require_duty_ingest),
):
    """
    【业务】程序式批准（JSON）：允许或拒绝当前 HITL 工具，并可 scope=session。
    【技术】POST /api/duty/decide?token=。查 duty_pending → 审计 → 作废 token →
            SSE 流式 resume_after_approval。和 card_decide 业务相同、通道不同
            （那边是手机 <a href> 只能 GET）。
    【是否主线】HITL。工作台更常用 chat.approve。
    """
    from app.models.audit import AuditRow
    from app.routers.chat import _sse, _stream
    from app.services.workspace import resolve_workspace as resolve_ws

    pending = await lookup_pending(db, token)
    if not pending:
        raise HTTPException(404, "批准已过期，请打开值班台")
    session_id = pending.session_id
    row = await db.get(SessionRow, session_id)
    if not row:
        raise HTTPException(404, "会话不存在")
    workspace = resolve_ws(row.workspace_path)
    db.add(
        AuditRow(
            session_id=row.id,
            tool_name="approval",
            arguments=body.scope,
            decision="allow" if body.approved else "deny",
            actor="card",
        )
    )
    await consume_pending(db, token)
    await db.commit()
    factory = get_session_factory()
    scope = body.scope if body.scope in {"once", "session"} else "once"

    async def events():
        collected = ""
        try:
            async for ev in resume_after_approval(
                row.id, workspace, row.mode, body.approved, scope == "session", row.model
            ):
                if ev.get("type") == "text_delta":
                    collected += ev.get("text") or ""
                yield _sse(ev)
            if collected:
                async with factory() as extra:
                    await add_message(extra, row.id, "assistant", collected)
        except Exception as exc:
            yield _sse({"type": "error", "message": str(exc)})

    return _stream(events())


@router.post("/test-notify")
async def test_notify(_user: CurrentUser = Depends(require_engineer)) -> dict:
    """
    【业务】测钉钉/企微通不通。不插收件箱真告警。
    【技术】假 id=test 调 notify_alert，返回实际发到的通道名。
    【是否主线】否。
    """
    sent = await notify_alert(
        {
            "id": "test",
            "title": "值班通道测试",
            "summary": "Forge 值班台连通性测试。点开链接应进入手机值班页。",
        }
    )
    return {"ok": True, "sent": sent, "channels": channel_status()}


@router.post("/alerts/{alert_id}/resolve")
async def resolve_alert(
    alert_id: str, db: AsyncSession = Depends(get_db), user: CurrentUser = Depends(require_engineer)
) -> dict:
    """
    【业务】人手点「关闭」这张告警。金丝雀变绿时 watchdog 也会自动关，这是人关。
    【技术】status=resolved。行还在表里，列表默认过滤掉。
    【是否主线】协作，不驱动 Agent。
    """
    row = await db.get(DutyAlertRow, alert_id)
    if not row:
        raise HTTPException(404, "告警不存在")
    row.status = "resolved"
    await db.commit()
    return {"ok": True, "alert": _public(row)}


class AssignIn(BaseModel):
    """【业务】指派给谁。空则接口里改用当前登录用户。"""
    username: str = ""


@router.post("/alerts/{alert_id}/ack")
async def ack_alert(
    alert_id: str, db: AsyncSession = Depends(get_db), user: CurrentUser = Depends(require_engineer)
) -> dict:
    """
    【业务】签收：这张纸条我看见了、我来跟。记下谁、何时。
            仅 open → acked；已在 investigating 只补签收人，不退回状态。
    【技术】acked_by / acked_at（UTC）。db 是这一次请求的库会话，不是整座 Postgres。
    【是否主线】协作。watchdog 去重把 acked 也当「未关闭」。
    """
    from datetime import datetime, timezone

    row = await db.get(DutyAlertRow, alert_id)
    if not row:
        raise HTTPException(404, "告警不存在")
    row.acked_by = user.username
    row.acked_at = datetime.now(timezone.utc)
    if row.status == "open":
        row.status = "acked"
    await db.commit()
    return {"ok": True, "alert": _public(row)}


@router.post("/alerts/{alert_id}/assign")
async def assign_alert(
    alert_id: str,
    body: AssignIn,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_engineer),
) -> dict:
    """
    【业务】把这题派给某个工程师。用户名为空则派给当前登录的人。
    【技术】只改 assigned_to（小写）。不改 status，也不开会话。
    【是否主线】协作。
    """
    row = await db.get(DutyAlertRow, alert_id)
    if not row:
        raise HTTPException(404, "告警不存在")
    row.assigned_to = (body.username or user.username).strip().lower()
    await db.commit()
    return {"ok": True, "alert": _public(row)}


def _card_page(title: str, body: str, actions: str = "") -> HTMLResponse:
    """
    【业务】手机批准卡的 HTML 壳：标题、正文、底部按钮。过期/成功/失败共用。
    【技术】HTMLResponse + html.escape，防止工具参数弄坏页面。不是 JSON。
    【是否主线】否。给 /card 和 /card-decide 用。
    """
    return HTMLResponse(
        f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>{html.escape(title)}</title>
  <style>
    body {{ margin:0; min-height:100vh; background:#080907; color:#d7dacd;
      font-family: system-ui, sans-serif; padding:24px; }}
    .card {{ max-width:420px; margin:12vh auto 0; border:1px solid #2c3228;
      background:#161913; border-radius:16px; padding:22px; }}
    h1 {{ font-size:18px; margin:0 0 12px; }}
    pre {{ white-space:pre-wrap; font-size:12px; color:#9db892; background:#10120d;
      padding:10px; border-radius:8px; max-height:40vh; overflow:auto; }}
    .row {{ display:flex; flex-wrap:wrap; gap:8px; margin-top:16px; }}
    a {{ display:inline-block; min-height:44px; line-height:44px; padding:0 16px;
      border-radius:10px; text-decoration:none; font-size:14px; font-weight:600; }}
    .ok {{ background:#e39b4a; color:#080907; }}
    .no {{ border:1px solid #2c3228; color:#d7dacd; }}
    .link {{ color:#8d9280; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>{html.escape(title)}</h1>
    {body}
    <div class="row">{actions}</div>
  </div>
</body>
</html>"""
    )


@router.get("/card")
async def approval_card(token: str, db: AsyncSession = Depends(get_db)) -> HTMLResponse:
    """
    【业务】钉钉/企微点开的「需要批准 write_file」小页，展示工具名和参数。
    【技术】GET /api/duty/card?token=。token 对 duty_pending；再拼允许/拒绝/回值班台链接。
            过期则仍回一页说明，不抛 404 JSON（手机浏览器要能看懂）。
    【是否主线】HITL 旁路。
    """
    pending = await lookup_pending(db, token)
    if not pending:
        return _card_page("批准已过期", "<p>请打开值班台重新发一条，或等下一次需要批准。</p>")
    state = await db.get(AgentStateRow, pending.session_id)
    tool = pending.tool or (state.pending_tool if state else "") or "tool"
    args = pending.arguments or (state.pending_arguments if state else "")
    allow = h5_url("/api/duty/card-decide", {"token": token, "approved": "1"})
    deny = h5_url("/api/duty/card-decide", {"token": token, "approved": "0"})
    bench = h5_url("/", {"duty": "1", "session": pending.session_id, "approve": token})
    body = f"<p>工具 <b>{html.escape(tool)}</b></p><pre>{html.escape(args or '（无参数）')}</pre>"
    actions = (
        f'<a class="ok" href="{html.escape(allow)}">允许</a>'
        f'<a class="no" href="{html.escape(deny)}">拒绝</a>'
        f'<a class="link" href="{html.escape(bench)}">打开值班台</a>'
    )
    return _card_page(f"需要批准 {tool}", body, actions)


@router.get("/card-decide")
async def card_decide(
    token: str,
    approved: bool = Query(...),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """
    【业务】手机大拇指点允许/拒绝。点完 Agent 在值班机继续跑，本页只提示结果。
    【技术】必须是 GET（<a href>）。作废 token、写审计、create_task 后台 resume。
            内存 interruptions 若因 API 重启丢了，只能提示去值班台。scope 固定 once。
    【是否主线】HITL 旁路。
    """
    pending = await lookup_pending(db, token)
    if not pending:
        return _card_page("批准已过期", "<p>这条批准已经用过，或 API 重启后中断对象丢了。请打开值班台。</p>")
    row = await db.get(SessionRow, pending.session_id)
    if not row:
        return _card_page("会话不存在", "<p>对应的值班会话已经删掉。</p>")
    from app.models.audit import AuditRow
    from app.services.workspace import resolve_workspace as resolve_ws

    workspace = resolve_ws(row.workspace_path)
    db.add(
        AuditRow(
            session_id=row.id,
            tool_name="approval",
            arguments="once",
            decision="allow" if approved else "deny",
            actor="card",
        )
    )
    await consume_pending(db, token)
    await db.commit()
    asyncio.create_task(
        _resume_in_background(
            row.id, workspace, row.mode, approved, row.model
        )
    )
    bench = h5_url("/", {"duty": "1", "session": row.id})
    verb = "已允许" if approved else "已拒绝"
    return _card_page(
        verb,
        "<p>Agent 在值班机上继续跑。打开值班台看对话和日志。</p>",
        f'<a class="ok" href="{html.escape(bench)}">打开值班台</a>',
    )


async def _resume_in_background(session_id, workspace, mode, approved: bool, model: str) -> None:
    """
    【业务】卡片页已经关掉，Agent 还得在值班机上继续；若又要批准就再推一张卡。
    【技术】后台任务。always=False（只批这一次）。收集 text_delta 后 add_message。
            自己 get_session_factory，因为请求的 db 会话此时已经关了。
    【谁调用】card_decide。decide_from_card 改走 SSE，不走这里。
    """
    factory = get_session_factory()
    collected = ""
    try:
        async for ev in resume_after_approval(session_id, workspace, mode, approved, False, model):
            if ev.get("type") == "text_delta":
                collected += ev.get("text") or ""
            if ev.get("type") == "done":
                collected = ev.get("text") or collected
            if ev.get("type") == "approval_needed":
                await notify_approval(
                    session_id, ev.get("tool") or "", ev.get("arguments") or ""
                )
        if collected:
            async with factory() as extra:
                await add_message(extra, session_id, "assistant", collected)
    except Exception as exc:
        log.warning("card resume failed: %s", exc)


@router.get("/target")
async def target_status(_auth: CurrentUser = Depends(require_token)) -> dict:
    """
    【业务】落地页仪表：order-api 在不在、金丝雀、当前题号。
    【技术】snapshot 会打 HTTP，丢到线程以免卡住事件循环。
    【是否主线】落地页。雷达自己也会调 snapshot，不经过这个接口。
    """
    from app.services.supervisor import snapshot

    return {"ok": True, **await asyncio.to_thread(snapshot)}


@router.post("/target/start")
async def target_start(_user: CurrentUser = Depends(require_engineer)) -> dict:
    """
    【业务】人手把小商店拉起来（API 启动时一般已自动拉）。
    【技术】线程里 start_target，再 snapshot 给前端刷新卡片。
    【是否主线】落地页。Agent 热修重启走工具 restart_service。
    """
    from app.services.supervisor import start_target, snapshot

    await asyncio.to_thread(start_target)
    return {"ok": True, **await asyncio.to_thread(snapshot)}


@router.post("/target/restart")
async def target_restart(_user: CurrentUser = Depends(require_engineer)) -> dict:
    """
    【业务】人手重启 :8790，让磁盘上的新代码进进程。
    【技术】线程里 restart_target。与 Agent 的 restart_service 同一套 supervisor。
    【是否主线】落地页手动按钮。
    """
    from app.services.supervisor import restart_target, snapshot

    await asyncio.to_thread(restart_target)
    return {"ok": True, **await asyncio.to_thread(snapshot)}


@router.post("/target/next-bug")
async def target_next_bug(_user: CurrentUser = Depends(require_engineer)) -> dict:
    """
    【业务】修好一题后点「下一题」：active_bug.txt +1（1～10 循环）并重启，雷达盯新题。
    【技术】set_active_bug → restart_target。旧 watchdog 告警绿了才会被 tick 关掉。
    【是否主线】演示切题，评测脚本自己写 active_bug.txt，不走这个接口。
    """
    from app.services.supervisor import active_bug, restart_target, set_active_bug, snapshot

    nxt = set_active_bug(active_bug() + 1)
    await asyncio.to_thread(restart_target)
    return {"ok": True, "active_bug": nxt, **await asyncio.to_thread(snapshot)}
