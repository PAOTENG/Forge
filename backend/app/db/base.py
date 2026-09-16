"""
SQLAlchemy ORM 基类。

【在 Agent 里的位置】
    所有业务表（会话、消息、agent_states、告警、备份等）都继承这个 Base，
    Alembic 才能扫到模型并建表。本文件没有运行时逻辑。

【调用关系】
    被 app/models/*.py 的每一张表 import。
    输入：无。输出：DeclarativeBase 子类 Base。
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """所有 Forge 表模型的共同基类。没有额外字段，只负责注册元数据。"""

    pass
