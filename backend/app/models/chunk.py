"""
工作区代码切块表，给 Agent 的 search_code 用。

【在 Agent 里的位置】
    打开会话时 sessions.create_session 后台 index_workspace，按约 80 行一块写入。
    模型调 search_code → indexer.search_chunks，目前是 pg_trgm ILIKE，不是向量检索。
    embedding 列预留 1024 维，未接独立 embedding 服务。

【调用关系】
    写入：indexer.index_workspace。
    读取：indexer.search_chunks ← tools.search_code。
"""

from datetime import datetime
from uuid import uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class FileChunkRow(Base):
    """
    【功能】一份工作区文件的一个文本块。
    【字段】
        workspace_key：工作区绝对路径的 sha256 前 32 位，换目录互不干扰。
        rel_path + chunk_index：同一文件内第几块（联合唯一）。
        content：块正文，检索用。
        embedding：预留，当前为 NULL。
    """

    __tablename__ = "file_chunks"
    __table_args__ = (UniqueConstraint("workspace_key", "rel_path", "chunk_index"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_key: Mapped[str] = mapped_column(String(64), index=True)
    rel_path: Mapped[str] = mapped_column(Text)
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
