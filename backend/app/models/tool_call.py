"""工具调用审计表：每次 function_tool 执行前后各落一行，哈希链防篡改。"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ToolCallRow(Base):
    """
    【功能】一次工具 start/end 的不可改写记录。
    【字段】
        prev_hash / hash：本会话内 SHA-256 链，改历史对不上。
        state：start | end | blocked。
        decision：allow | redact | reject（guardrail/DLP 结论，可空）。
    """

    __tablename__ = "tool_calls"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    tool_name: Mapped[str] = mapped_column(String(80), default="")
    tool_call_id: Mapped[str] = mapped_column(String(80), default="")
    agent_name: Mapped[str] = mapped_column(String(80), default="")
    arguments: Mapped[str] = mapped_column(Text, default="")
    output: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(String(40), default="")
    state: Mapped[str] = mapped_column(String(16), default="start")
    decision: Mapped[str] = mapped_column(String(16), default="")
    prev_hash: Mapped[str] = mapped_column(String(64), default="")
    hash: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
