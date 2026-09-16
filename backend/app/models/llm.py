"""
用户自定义模型目录行。

【在 Agent 里的位置】
    右上角菜单除了内置 DeepSeek/GLM/千问/Grok，还可以加一条 model_id。
    真正发请求仍走 agent_runtime.resolve_model（要有对应 Key）。

【调用关系】
    routers/models.py 增删改查。
"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CustomModelRow(Base):
    """
    【功能】一条自定义模型。
    【字段】provider 用来归到菜单分组；label 给人看；model_id 给 LiteLLM。
    """

    __tablename__ = "custom_models"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    provider: Mapped[str] = mapped_column(String(40), default="custom")
    label: Mapped[str] = mapped_column(String(80))
    model_id: Mapped[str] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
