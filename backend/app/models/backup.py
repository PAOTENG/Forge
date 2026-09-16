"""
文件写前备份的元数据表（磁盘 blob 在 data/backups/）。

【在 Agent 里的位置】
    write_file / delete_file 真正改盘前，backup.snapshot_before_change 插一行，
    并把改前内容拷到 data/backups/{session}/{id}/original。工作台「回退」按这张表恢复。

【调用关系】
    写入：services/backup.py。
    读取：sessions.list_backups / restore_*；watchdog 每小时 purge_expired。
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class FileBackupRow(Base):
    """
    【功能】一次写/删的快照指针。
    【字段】
        rel_path：相对工作区的路径。
        action：write / delete。
        existed：改之前文件是否存在（False 表示新建，回退时应删掉）。
        blob_dir：磁盘目录，内含 original / incoming / NOTES.txt。
        expires_at：默认 7 天；git_push 成功后从推送时刻再计 7 天。
    """

    __tablename__ = "file_backups"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    rel_path: Mapped[str] = mapped_column(String(500), default="")
    action: Mapped[str] = mapped_column(String(20), default="write")
    existed: Mapped[bool] = mapped_column(Boolean, default=True)
    actor: Mapped[str] = mapped_column(String(40), default="")
    model: Mapped[str] = mapped_column(String(80), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    blob_dir: Mapped[str] = mapped_column(String(400), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    pushed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    restored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
