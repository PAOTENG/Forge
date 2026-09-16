"""
值班告警与卡片批准 token。

【在 Agent 里的位置】
    watchdog 金丝雀失败 → DutyAlertRow 进收件箱 → 人点排查开会话。
    变更工具要批准时，duty.notify_approval 发钉钉/企微卡，token 记在 DutyPendingRow，
    API 重启后卡片还能对上 session_id（内存 interruptions 仍可能过期）。

【调用关系】
    DutyAlertRow：watchdog.tick、duty.ingest、duty.investigate。
    DutyPendingRow：duty.persist_pending / lookup_pending / consume_pending。
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DutyAlertRow(Base):
    """
    【功能】收件箱里的一条告警。
    【字段】
        source：watchdog / webhook / demo。
        status：open / acked / investigating / resolved。
        session_id：点「排查」后挂上的会话，可空。
        stack / link：金丝雀 body 和 /canary URL，会写进排查 prompt。
    """

    __tablename__ = "duty_alerts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    title: Mapped[str] = mapped_column(String(240), default="未命名告警")
    summary: Mapped[str] = mapped_column(Text, default="")
    service: Mapped[str] = mapped_column(String(120), default="")
    severity: Mapped[str] = mapped_column(String(32), default="warning")
    source: Mapped[str] = mapped_column(String(40), default="webhook")
    stack: Mapped[str] = mapped_column(Text, default="")
    link: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="open")
    session_id: Mapped[str | None] = mapped_column(
        ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True
    )
    assigned_to: Mapped[str] = mapped_column(String(40), default="")
    acked_by: Mapped[str] = mapped_column(String(40), default="")
    acked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DutyPendingRow(Base):
    """
    【功能】批准卡片上的短 token → 会话映射。
    【字段】token 主键；session_id；当时要批的 tool / arguments 摘要。
    【生命周期】notify_approval 插入，卡片点完 consume_pending 删除。
    """

    __tablename__ = "duty_pending"

    token: Mapped[str] = mapped_column(String(32), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"))
    tool: Mapped[str] = mapped_column(String(80), default="")
    arguments: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
