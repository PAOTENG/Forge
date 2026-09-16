"""
工作台插件是否已“安装”到 data/plugins/。

【在 Agent 里的位置】
    这不是 Agent 工具，是值班台打开 md/docx/xlsx 的查看器开关。
    进入工作台会把 plugin_store 拷到磁盘；本表记启用状态。

【调用关系】
    routers/plugins.py 读写。
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class InstalledPluginRow(Base):
    """
    【功能】plugin_id 是否启用。内置查看器不依赖本表也会工作。
    """

    __tablename__ = "installed_plugins"

    plugin_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    installed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
