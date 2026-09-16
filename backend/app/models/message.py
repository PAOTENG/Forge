"""
消息表：工作台聊天气泡和工具调用日志。

【在 Agent 里的位置】
    用户发言、模型最终回答、每次 tool_called 都会 add_message。
    这是给人看的历史；给模型续聊的是 agent_states.input_items，两套并行。

【调用关系】
    写入：messages.add_message（chat 发消息、Runtime._produce 记工具）。
    读取：sessions.list_messages 刷新后拉气泡。
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.session import SessionRow


class MessageRow(Base):
    """
    【功能】一条可见消息。
    【字段】
        role：user / assistant / system。
        kind：空=普通文本；tool=工具调用摘要（tool 名 + 参数）。
        content：正文；入库前会剥掉 Postgres 不收的 \\x00。
    """

    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(32), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    session: Mapped[SessionRow] = relationship(back_populates="messages")
