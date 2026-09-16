"""
写前快照与回退：磁盘 blob + Postgres 元数据。

【在 Agent 里的位置】
    write_file/delete_file 改盘前 snapshot_before_change。
    工作台「回退」按文件或整会话 restore。git_push 成功后 TTL 从推送再计 7 天。

【调用关系】
    tools.write_file / delete_file；sessions.restore_*；watchdog.purge；git_push.mark_session_pushed。
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timedelta, timezone
from difflib import unified_diff
from pathlib import Path
from uuid import uuid4

from app.core.config import REPO_ROOT, get_settings
from app.db.session import get_session_factory
from app.models.backup import FileBackupRow

log = logging.getLogger("forge.backup")

TTL_DAYS = 7
BACKUP_ROOT = REPO_ROOT / "data" / "backups"
LOG_ROOT = REPO_ROOT / "data" / "logs"


def _now() -> datetime:
    """【功能】UTC 现在，给 expires_at / restored_at 用。"""
    return datetime.now(timezone.utc)


def _rel(workspace: Path, target: Path) -> str:
    """【功能】备份记录里存相对路径 posix。越界会 ValueError。"""
    return target.resolve().relative_to(workspace.resolve()).as_posix()


def write_note(folder: Path, payload: dict) -> None:
    """【功能】写 NOTES.txt，给人在 data/backups 里肉眼看是谁改的。"""
    folder.mkdir(parents=True, exist_ok=True)
    lines = [
        f"时间：{payload.get('created_at')}",
        f"修改人：{payload.get('actor')}",
        f"会话：{payload.get('session_id')}",
        f"模型：{payload.get('model')}",
        f"动作：{payload.get('action')}",
        f"文件：{payload.get('rel_path')}",
        f"改前是否存在：{payload.get('existed')}",
        "修改说明：",
        payload.get("summary") or "（无摘要）",
        "",
        "完整元数据：",
        json.dumps(payload, ensure_ascii=False, indent=2),
        "",
    ]
    (folder / "NOTES.txt").write_text("\n".join(lines), encoding="utf-8")


def _copy_process_log(folder: Path) -> None:
    """【功能】顺带截一段 order-api 进程日志，方便对照金丝雀当时的状态。"""
    src = REPO_ROOT / "data" / "targets" / "order-api.log"
    if not src.is_file():
        return
    try:
        text = src.read_text(encoding="utf-8", errors="replace")
        tail = "\n".join(text.splitlines()[-120:])
        (folder / "process.log").write_text(tail, encoding="utf-8")
        dest = LOG_ROOT / f"{_now().strftime('%Y%m%d-%H%M%S')}-{src.name}"
        LOG_ROOT.mkdir(parents=True, exist_ok=True)
        dest.write_text(tail, encoding="utf-8")
    except OSError:
        log.warning("copy process log failed")


async def snapshot_before_change(
    session_id: str,
    workspace: Path,
    target: Path,
    action: str,
    actor: str,
    model: str,
    incoming: str = "",
) -> FileBackupRow | None:
    """
    【功能】改盘前：拷 original/incoming、算 diff、写 NOTES、插 file_backups。
    【输入】session_id、workspace、target 绝对路径、action=write|delete、actor、model、incoming 新内容。
    【输出】FileBackupRow；路径越界则 None。
    """
    workspace = workspace.resolve()
    target = target.resolve()
    try:
        rel = _rel(workspace, target)
    except ValueError:
        return None
    existed = target.is_file()
    original = ""
    if existed:
        original = target.read_text(encoding="utf-8", errors="replace")
    bid = str(uuid4())
    folder = BACKUP_ROOT / session_id / bid
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "original").write_text(original, encoding="utf-8")
    if incoming:
        (folder / "incoming").write_text(incoming, encoding="utf-8")
    diff = ""
    if existed and incoming:
        diff = "".join(
            unified_diff(
                original.splitlines(True),
                incoming.splitlines(True),
                fromfile=f"a/{rel}",
                tofile=f"b/{rel}",
                n=3,
            )
        )[:8000]
    summary = diff or (("删除文件" if action == "delete" else "新建文件") if not existed else f"覆盖 {len(original)} 字符")
    created = _now()
    expires = created + timedelta(days=TTL_DAYS)
    payload = {
        "id": bid,
        "session_id": session_id,
        "rel_path": rel,
        "action": action,
        "existed": existed,
        "actor": actor or "local",
        "model": model or get_settings().forge_model or "deepseek/deepseek-chat",
        "summary": summary,
        "created_at": created.isoformat(),
        "expires_at": expires.isoformat(),
    }
    write_note(folder, payload)
    _copy_process_log(folder)
    row = FileBackupRow(
        id=bid,
        session_id=session_id,
        rel_path=rel,
        action=action,
        existed=existed,
        actor=payload["actor"],
        model=payload["model"],
        summary=summary[:8000],
        blob_dir=str(folder),
        created_at=created,
        expires_at=expires,
    )
    factory = get_session_factory()
    async with factory() as db:
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return row


async def list_session_backups(session_id: str) -> list[dict]:
    """【功能】每个相对路径只展示最早一条，带 versions 计数。给回退弹窗。"""
    from sqlalchemy import select

    factory = get_session_factory()
    async with factory() as db:
        rows = (
            await db.execute(
                select(FileBackupRow)
                .where(FileBackupRow.session_id == session_id)
                .order_by(FileBackupRow.created_at.asc())
            )
        ).scalars().all()
    grouped: dict[str, FileBackupRow] = {}
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.rel_path] = counts.get(row.rel_path, 0) + 1
        grouped.setdefault(row.rel_path, row)
    out = []
    for rel, row in grouped.items():
        out.append(
            {
                "id": row.id,
                "rel_path": rel,
                "action": row.action,
                "existed": row.existed,
                "actor": row.actor,
                "model": row.model,
                "summary": row.summary[:1200],
                "created_at": row.created_at.isoformat() if row.created_at else "",
                "pushed_at": row.pushed_at.isoformat() if row.pushed_at else "",
                "expires_at": row.expires_at.isoformat() if row.expires_at else "",
                "restored_at": row.restored_at.isoformat() if row.restored_at else "",
                "versions": counts.get(rel, 1),
            }
        )
    return out


def _apply_row(workspace: Path, row: FileBackupRow) -> str:
    """【功能】按一行备份还原：原来不存在则删新文件，否则写回 original。"""
    dest = (workspace / row.rel_path).resolve()
    dest.relative_to(workspace.resolve())
    original_path = Path(row.blob_dir) / "original"
    original = original_path.read_text(encoding="utf-8") if original_path.is_file() else ""
    if not row.existed:
        if dest.is_file():
            dest.unlink()
        return f"removed {row.rel_path}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(original, encoding="utf-8")
    return f"restored {row.rel_path}"


async def restore_file(session_id: str, backup_id: str, workspace: Path) -> str:
    """【功能】回退单个文件并标 restored_at。"""
    factory = get_session_factory()
    async with factory() as db:
        row = await db.get(FileBackupRow, backup_id)
        if not row or row.session_id != session_id:
            raise FileNotFoundError("没有这条备份")
        msg = _apply_row(workspace, row)
        row.restored_at = _now()
        await db.commit()
        return msg


async def restore_all(session_id: str, workspace: Path) -> list[str]:
    """【功能】按时间倒序回退本会话所有文件（后改的先还原）。"""
    items = await list_session_backups(session_id)
    done = []
    for item in reversed(items):
        done.append(await restore_file(session_id, item["id"], workspace))
    return done


async def mark_session_pushed(session_id: str) -> None:
    """【功能】git_push 成功后重计 7 天过期，避免推完立刻被清。"""
    from sqlalchemy import select

    factory = get_session_factory()
    now = _now()
    exp = now + timedelta(days=TTL_DAYS)
    async with factory() as db:
        rows = (
            await db.execute(select(FileBackupRow).where(FileBackupRow.session_id == session_id))
        ).scalars().all()
        for row in rows:
            row.pushed_at = now
            row.expires_at = exp
        await db.commit()


async def purge_expired() -> int:
    """【功能】删过期 blob 和行，并清旧进程日志。输出：删了几条。watchdog 每小时调。"""
    from sqlalchemy import select

    factory = get_session_factory()
    now = _now()
    removed = 0
    async with factory() as db:
        rows = (
            await db.execute(select(FileBackupRow).where(FileBackupRow.expires_at < now))
        ).scalars().all()
        for row in rows:
            folder = Path(row.blob_dir)
            shutil.rmtree(folder, ignore_errors=True)
            await db.delete(row)
            removed += 1
            parent = folder.parent
            try:
                if parent.is_dir() and not any(parent.iterdir()):
                    parent.rmdir()
            except OSError:
                pass
        await db.commit()
    cutoff = now - timedelta(days=TTL_DAYS)
    if LOG_ROOT.is_dir():
        for path in LOG_ROOT.glob("*"):
            try:
                mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            except OSError:
                continue
            if mtime < cutoff:
                path.unlink(missing_ok=True)
    return removed
