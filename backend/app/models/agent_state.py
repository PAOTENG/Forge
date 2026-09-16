"""
Agent 结构化状态表：给模型续聊用的 input_items。

【在 Agent 里的位置】
    OpenAI Agents SDK 的历史是一串 item（user / function_call / function_call_output）。
    API 一重启内存里的 Runner 没了，必须从这里 load_input_items 再跑。
    pending_tool 让前端刷新后还能画出「等待批准 write_file」条。

【对应简历】
    “结构化历史和待审批信息写入 PostgreSQL；审批对象失效时补明确结果。”

【调用关系】
    写入：agent_runtime.save_agent_state。
    读取：load_input_items、sessions.get_session 拼 pending。
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.session import SessionRow


class AgentStateRow(Base):
    """
    【功能】每个会话一行，主键就是 session_id。
    【字段】
        input_items：SDK to_input_list() 的 JSON 数组。
        pending_tool / pending_arguments：当前卡在 HITL 的工具名和参数 JSON。
    【注意】中断对象（interruptions）仍在 API 进程内存；重启后 token 还在，
        但 start_resume 会因 result 丢失而抛 STALE_APPROVAL，需用户重发。
    """

    __tablename__ = "agent_states"

    session_id: Mapped[str] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), primary_key=True
    )
    input_items: Mapped[list] = mapped_column(JSONB, default=list)
    pending_tool: Mapped[str] = mapped_column(Text, default="")
    pending_arguments: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    session: Mapped[SessionRow] = relationship(back_populates="agent_state")
