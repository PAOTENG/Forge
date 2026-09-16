"""
值班机本地账号与登录会话。

【在 Agent 里的位置】
    首次打开网页 setup 建管理员；之后工程师才能发消息、批准改码。
    无 SSO。机器 Token 不走这张表，走 Settings.forge_api_token。

【调用关系】
    auth 路由读写；security.require_user 用 token 查 AuthSessionRow 再拿 UserRow。
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class UserRow(Base):
    """
    【功能】一个本地用户。
    【字段】username 唯一；password_hash 见 security.hash_password；
        role：admin / engineer / viewer。
    """

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    username: Mapped[str] = mapped_column(String(40), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(20), default="engineer")
    display_name: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuthSessionRow(Base):
    """
    【功能】登录 cookie 对应的服务端会话。
    【字段】token 主键（也是 cookie 值）；expires_at 过期后 require_user 当未登录。
    """

    __tablename__ = "auth_sessions"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
