"""
聊天记录入库。给人看的气泡，不是 SDK 的 input_items。

【在 Agent 里的位置】
    用户发言、模型最终回答、每次工具调用摘要都走 add_message。
    刷新页面 list_messages 靠这张表。

【调用关系】
    chat.send_message、agent_runtime._produce、duty.investigate。
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.message import MessageRow


async def add_message(
    db: AsyncSession,
    session_id: str,
    role: str,
    content: str,
    kind: str = "",
    commit: bool = True,
) -> MessageRow:
    """
    【功能】插一条 messages。剥掉 \\x00，避免 Postgres 报错。
    【输入】
        db：调用方会话（可先不 commit，和改 title 同事务）。
        session_id / role / content / kind（tool 表示工具日志）。
        commit：False 时由调用方一起提交。
    【输出】刚插入的 MessageRow。
    """
    row = MessageRow(
        session_id=session_id,
        role=role,
        content=(content or "").replace("\x00", ""),
        kind=(kind or "").replace("\x00", ""),
    )
    db.add(row)
    if commit:
        await db.commit()
    return row
