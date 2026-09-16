"""
工作台「对话」相关的网址：发消息、刷新后续看、停止、点允许/拒绝。

值班台点「排查」之后，duty.investigate 只建好了一次对话，模型还没开口。
人打字（或网页自动把排查稿发出去）会打到本文件的 send_message，这里才叫模型开始干活。
刷新页面不要把同一句话再发一遍，用 follow_events 接着看已经在出的字。
模型要改文件时会停住；网页点允许走 approve。

本文件不写工具、不看金丝雀。真正跑模型在 agent_runtime。

调用总图：
    send_message → 存用户气泡 → start_prompt 开跑 → _relay 把字推给网页
    follow_events → 还是 _relay（接着看同一轮，不重开模型）
    approve → 先记一笔谁批的 → start_resume 接着跑 → 还是 _relay
    事件里出现「需要批准」→ _maybe_notify_approval → 手机再推一张卡
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import CurrentUser, require_engineer
from app.db.session import get_db
from app.models.audit import AuditRow
from app.models.session import SessionRow
from app.schemas import ApprovalIn, MessageIn
from app.services.agent_runtime import (
    cancel_run,
    is_active,
    start_prompt,
    start_resume,
    subscribe_events,
)
from app.services.messages import add_message
from app.services.project_notes import is_placeholder_title, title_from_prompt, with_open_file
from app.services.workspace import resolve_workspace

router = APIRouter(prefix="/sessions", tags=["chat"], dependencies=[Depends(require_engineer)])


async def _row(db: AsyncSession, session_id: str) -> SessionRow:
    """
    【业务】先问数据库：这个对话 id 还在不在。不在就别发消息、别批准，免得对着空气干活。
    【技术】用主键去 sessions 表取一行（SessionRow）。没有就对浏览器说 404。
    【是否主线】帮手。本身不跑模型，但后面几个接口都会先过这里。
    【谁调用】send_message、follow_events、cancel_message、approve。
    """
    row = await db.get(SessionRow, session_id)
    if not row:
        raise HTTPException(404, "Session not found")
    return row


def _sse(payload: dict) -> str:
    """
    【业务】把「模型刚吐出的一小段字 / 要用哪个工具」这种一件事，包装成网页能读的一行。
    【技术】网页用 EventSource 收这种流，约定格式叫 SSE：一行 `data: {json}` 再空一行。
            这里只负责把字典变成那一行字，不负责发送。
    【是否主线】帮手，给 _relay 用。
    【谁调用】_relay。
    """
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _stream(events):
    """
    【业务】告诉网站：别等模型把整段话说完再返回，一直开着，有一点推一点。
            这样工作台才能一边生成一边出字。
    【技术】返回 StreamingResponse，类型是 text/event-stream（就是上面说的 SSE）。
            关掉缓存；否则中间的 Nginx 可能把字攒着不推，网页会卡住半天突然出来一堆。
    【是否主线】帮手。send_message / follow_events / approve 最后都走它。
    【谁调用】上面三个接口的 return。
    """
    return StreamingResponse(
        events,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


def _maybe_notify_approval(session_id: str, ev: dict) -> None:
    """
    【业务】模型要改文件了。网页这时已经能弹出「批不批」；
            如果你不在工位，再往钉钉/企微推一张手机卡，人在外面也能点。
    【技术】只处理 type 等于 approval_needed 的那一条，别的事件直接忽略。
            create_task = 另开一个后台去推卡，不等推完，免得字卡住不往网页走。
    【是否主线】HITL 帮手（HITL = 改文件前必须有人点头）。网页批准不靠它，手机卡靠它。
    【谁调用】_relay 每收到一件事都会问它一声。
    """
    if ev.get("type") != "approval_needed":
        return
    from app.services.duty import notify_approval

    asyncio.create_task(
        notify_approval(session_id, ev.get("tool") or "", ev.get("arguments") or "")
    )


async def _relay(session_id: str, after: int = 0):
    """
    【业务】坐在这条对话旁边听：模型每出一点东西，就转给网页。
            after 是「我已经看到第几条了」。刷新时带上它，后面的再给我，别从头重放一遍。
    【技术】subscribe_events 是内存里的队列，不是数据库。每来一件事 yield 一行给网页。
            大约 12 秒没动静会来一条 ping。网页不用显示 ping，改写成 `: ping`（SSE 注释），
            只为了让中间的代理别以为连接死了把线掐掉。
    【是否主线】是。没有它，网页收不到正在生成的字。
    【谁调用】send_message、follow_events、approve 最后都 return _stream(_relay(...))。
    """
    try:
        async for ev in subscribe_events(session_id, after=after):
            if ev.get("type") == "ping":
                yield ": ping\n\n"
                continue
            _maybe_notify_approval(session_id, ev)
            yield _sse(ev)
    except asyncio.CancelledError:
        return


@router.post("/{session_id}/messages")
async def send_message(
    session_id: str,
    body: MessageIn,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_engineer),
) -> StreamingResponse:
    """
    【业务】这是「把一句话交给模型」的网址，函数自己不会定时发。谁来调用，见下面两种。

            第一种（热修主线，人点了按钮，网页再自动发）：
            你在收件箱点「排查」→ investigate 只建好对话，返回一段写好的任务说明
            （里面有 BUG 题号、金丝雀正文、先定位再改等）。网页看到「这是新开会话」
            就会立刻 POST 到这里，content 就是那段任务说明。你没有在输入框打字，
            但等于工作台替你把第一句发出去了。若这张告警以前排透过（resumed），
            网页不会再发，避免同一段说明跑第二遍。

            第二种（你自己发）：输入框打字回车，或点「定位/测试/推送/重启」快捷按钮。
            content 就是你打的字，或按钮对应的那句固定提示。

            「气泡」= 聊天窗口里的一条。用户那一侧是蓝色/你说的话；模型回答是另一侧。
            网页会先在屏幕上画出这条用户气泡；本函数再把它写入 messages 表，刷新后还能看见。
            body.content 就是这一条的正文（排查稿或你打的字），不是别的神秘内容。

            「模型没在跑」= 内存里没有正在生成、也没有卡在「等你点允许」。
            这时才存气泡、改标题、start_prompt。
            「已经在跑」= 字还在往外蹦，或弹窗在等批准。这时 if 不进去，不存第二份、不开第二轮，
            只把当前网页接到已经在出的字上（防止连点发送答两遍）。

            标题：侧栏那一行。新会话常是「值班 · …」或 New session，用第一句的前约 40 字换掉。
    【技术】POST /api/sessions/{id}/messages。开跑靠 start_prompt。
            编辑器开着文件时，with_open_file 在正文前加一行「当前打开：路径」。
    【是否主线】是。watchdog → 点排查 → 网页代发第一句 → 到这里模型才开始。
    【谁调用】前端 dispatchPrompt（排查成功后、回车、快捷按钮、重试都会走到它）。
    【下一步】_relay 把模型的字推到网页；要改文件时 type=approval_needed，等人点 approve。
    """
    row = await _row(db, session_id)
    workspace = resolve_workspace(row.workspace_path)
    if not is_active(row.id):
        await add_message(db, row.id, "user", body.content, commit=False)
        if is_placeholder_title(row.title, workspace.name):
            titled = title_from_prompt(body.content)
            if titled:
                row.title = titled
        await db.commit()
        await start_prompt(
            row.id,
            workspace,
            row.mode,
            with_open_file(body.content, body.open_path),
            row.model,
            actor=user.username,
            role=user.role,
        )
    return _stream(_relay(row.id))


@router.get("/{session_id}/events")
async def follow_events(
    session_id: str,
    after: int = Query(0),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """
    【业务】你刷新了页面，或切走又回来。用户那句话已经发过了，不要再发一遍
            （再发一遍模型会当成新问题再答一次）。从 after 这个序号接着看同一轮输出。
    【技术】GET /sessions/{id}/events?after=数字。只检查对话还在，不调用 start_prompt。
    【是否主线】是。没有它，刷新后面会断、或者从头再播一遍。
    【谁调用】前端 EventSource 重连（网页自带的「一直听服务器说话」）。
    """
    await _row(db, session_id)
    return _stream(_relay(session_id, after=after))


@router.post("/{session_id}/cancel")
async def cancel_message(session_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    """
    【业务】工作台点「停止」：这一轮模型别再说了，工具也别再调了。
    【技术】cancel_run 只在内存里打一个「请停」标记。对话还在，收件箱那张告警也不会被关掉。
    【是否主线】旁路。热修演示不一定要点停止。
    【谁调用】工作台停止按钮。
    """
    await _row(db, session_id)
    return {"ok": cancel_run(session_id)}


@router.post("/{session_id}/approvals")
async def approve(
    session_id: str,
    body: ApprovalIn,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_engineer),
) -> StreamingResponse:
    """
    【业务】工作台弹出「允许这次改文件吗」时，你点允许或拒绝。
            允许：模型接着改。拒绝：这一次工具不执行，模型会收到拒绝，再想别的办法。
    【技术】先往 audits 表记一笔（谁点的、允许还是拒绝、只批一次还是这轮会话都先别问），
            再 start_resume 让刚才停住的那一步继续。scope=session 时，后面同类工具本会话不再打断。
    【是否主线】是。改文件前必须有人点头（HITL）。手机钉钉卡走 duty.card_decide，最后也是 resume，只是入口不同。
    【谁调用】工作台允许/拒绝按钮。
    【下一步】还是 _relay 把后续的字推给网页。
    """
    row = await _row(db, session_id)
    workspace = resolve_workspace(row.workspace_path)
    db.add(
        AuditRow(
            session_id=row.id,
            tool_name="approval",
            arguments=body.scope,
            decision="allow" if body.approved else "deny",
            actor=user.username,
        )
    )
    await db.commit()
    await start_resume(
        row.id,
        workspace,
        row.mode,
        body.approved,
        body.scope == "session",
        row.model,
        actor=user.username,
        role=user.role,
    )
    return _stream(_relay(row.id))
