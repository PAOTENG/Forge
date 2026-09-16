"""
批准审计表。

【在 Agent 里的位置】
    人在值班台或钉钉卡片上点允许/拒绝时，chat.approve 插一行。
    方便事后回答“谁批准了 write_file / git_push”。

【调用关系】
    写入：routers/chat.approve。
    读取：sessions.list_audits。
"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AuditRow(Base):
    """
    【功能】一次 HITL 决策记录。
    【字段】
        tool_name：这里固定记 “approval”（具体工具名在 arguments/scope 和 pending 里）。
        arguments：scope，once 或 session。
        decision：allow / deny。
        actor：登录用户名。
    """

    __tablename__ = "audits"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    tool_name: Mapped[str] = mapped_column(String(80))
    arguments: Mapped[str] = mapped_column(Text, default="")
    decision: Mapped[str] = mapped_column(String(32))
    actor: Mapped[str] = mapped_column(String(40), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
