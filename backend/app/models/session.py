"""
会话表：一次“打开工作区 + 跟 Agent 对话”的容器。

【在 Agent 里的位置】
    值班员点「排查」或工作台「新会话」会插一行。后续消息、agent_states、备份都挂 session_id。
    刷新页面用 localStorage 里的 id 再 GET 这一行，恢复工作区和 mode。

【调用关系】
    写入：routers/sessions.create_session、duty.investigate。
    读取：chat 发消息前 _row()、attach_session 挂沙箱。
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.agent_state import AgentStateRow
    from app.models.message import MessageRow


class SessionRow(Base):
    """
    【功能】描述一次 Agent 会话的元数据（不是 SDK 的对话历史本身）。
    【字段】
        id：UUID，SSE 和 Runtime 字典的 key。
        title：侧栏显示名，首条用户消息后由 title_from_prompt 改掉。
        workspace_path：工具读写的根目录。
        model：本会话锁定的模型 id。
        mode：plan / build，决定变更工具是否 is_enabled。
    【关联】messages 一对多；agent_state 一对一。
    """

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    title: Mapped[str] = mapped_column(String(200), default="New session")
    workspace_path: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(120), default="")
    mode: Mapped[str] = mapped_column(String(20), default="build")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    messages: Mapped[list[MessageRow]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    agent_state: Mapped[AgentStateRow | None] = relationship(
        back_populates="session", cascade="all, delete-orphan", uselist=False
    )
