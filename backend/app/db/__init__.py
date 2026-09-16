"""
数据库访问出口。

【在 Agent 里的位置】
    Agent 要把对话历史、待审批工具、告警、备份元数据写入 PostgreSQL。
    本文件把会话工厂再导出一层，方便 `from app.db import get_db`。

【本目录做什么】
    session.py 建异步引擎；base.py 是 ORM 基类。

【对外导出】
    get_db / get_engine / get_session_factory
"""

from .session import get_db, get_engine, get_session_factory

__all__ = ["get_db", "get_engine", "get_session_factory"]
