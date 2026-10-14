"""
会话 CRUD、工作区文件树、diff 预览、备份回退、挂沙箱。

【在 Agent 里的位置】
    开会话 = 选工作区 + 起沙箱 + 后台建索引。Agent 对话走 chat.py，这里管「工作台」。
"""

from __future__ import annotations

import asyncio
import shutil
from difflib import unified_diff
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import CurrentUser, require_engineer, require_token, require_write
from app.db.session import get_db, get_session_factory
from app.models.agent_state import AgentStateRow
from app.models.audit import AuditRow
from app.models.message import MessageRow
from app.models.session import SessionRow
from app.models.tool_call import ToolCallRow
from app.schemas import FileSaveIn, FsEntryIn, FsRenameIn, MessageOut, SessionCreate, SessionOut, WritePreviewIn
from app.services.agent_runtime import has_live_approval, is_active, model_label, run_status
from app.services.tool_audit import verify_chain
from app.services.indexer import index_workspace
from app.services.sandbox import SandboxError, sandbox_manager
from app.services.viewers import classify
from app.services.workspace import WorkspaceError, resolve_under_workspace, resolve_workspace

router = APIRouter(prefix="/sessions", tags=["sessions"], dependencies=[Depends(require_token)])


def _out(row: SessionRow, pending_tool: str = "", pending_arguments: str = "") -> SessionOut:
    return SessionOut(
        id=row.id,
        title=row.title,
        workspace_path=row.workspace_path,
        model=row.model,
        mode=row.mode,
        pending_tool=pending_tool,
        pending_arguments=pending_arguments,
        live_run=is_active(row.id),
        run_status=run_status(row.id),
    )


async def _row(db: AsyncSession, session_id: str) -> SessionRow:
    row = await db.get(SessionRow, session_id)
    if not row:
        raise HTTPException(404, "Session not found")
    return row


@router.post("", response_model=SessionOut)
async def create_session(
    body: SessionCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_token),
) -> SessionOut:
    """
    【业务】工作台点「新会话」：选定工作区后立刻 ensure 沙箱，后面模型 bash 才有笼子。
            不是等第一句消息才起容器。索引在后台切，避免开会话转圈。
    【什么时候用】侧栏新建，不是值班点排查（排查走 duty.investigate，同样会 ensure）。
    【谁调用】前端 create session。
    【里面调用】resolve_workspace、插 SessionRow、sandbox_manager.ensure、index_workspace。
    """
    require_write(user)
    try:
        workspace = resolve_workspace(body.workspace_path)
    except WorkspaceError as exc:
        raise HTTPException(400, str(exc)) from exc
    row = SessionRow(
        title=body.title or workspace.name,
        workspace_path=str(workspace),
        model=body.model or model_label(),
        mode=body.mode,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    try:
        sandbox_manager.ensure(row.id, workspace)
    except SandboxError as exc:
        raise HTTPException(500, str(exc)) from exc

    async def _index() -> None:
        factory = get_session_factory()
        async with factory() as extra:
            try:
                await index_workspace(extra, workspace)
            except Exception:
                await extra.rollback()

    asyncio.create_task(_index())
    return _out(row)


@router.get("", response_model=list[SessionOut])
async def list_sessions(db: AsyncSession = Depends(get_db)) -> list[SessionOut]:
    """【功能】侧栏会话列表。"""
    rows = (
        await db.execute(select(SessionRow).order_by(SessionRow.created_at.desc()))
    ).scalars()
    return [_out(r) for r in rows]


@router.get("/{session_id}", response_model=SessionOut)
async def get_session(session_id: str, db: AsyncSession = Depends(get_db)) -> SessionOut:
    """【功能】单个会话详情，带 pending_tool / live_run。"""
    return _out(await _row(db, session_id))


@router.post("/{session_id}/attach", response_model=SessionOut)
async def attach_session(session_id: str, db: AsyncSession = Depends(get_db)) -> SessionOut:
    """
    【业务】你刷新了页面。容器可能还在，也可能被停过：再 ensure 一次挂上沙箱。
            若内存里批准还在，把 pending_tool 带回去，好画出「允许写文件」条。
    【什么时候用】工作台重新打开已有会话。
    【谁调用】前端 attach。
    【里面调用】sandbox_manager.ensure、has_live_approval。
    """
    row = await _row(db, session_id)
    workspace = resolve_workspace(row.workspace_path)
    try:
        sandbox_manager.ensure(row.id, workspace)
    except SandboxError as exc:
        raise HTTPException(500, str(exc)) from exc
    state = await db.get(AgentStateRow, row.id)
    live = has_live_approval(row.id)
    return _out(
        row,
        state.pending_tool if state and live else "",
        state.pending_arguments if state and live else "",
    )


@router.patch("/{session_id}", response_model=SessionOut)
async def update_session(
    session_id: str,
    mode: str | None = None,
    model: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> SessionOut:
    row = await _row(db, session_id)
    if mode in {"plan", "build"}:
        row.mode = mode
    if model and model.strip():
        row.model = model.strip()
    await db.commit()
    await db.refresh(row)
    return _out(row)


@router.delete("/{session_id}")
async def delete_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_token),
) -> dict:
    """
    【业务】删掉这次对话：先拆沙箱（stop），再删备份目录和 sessions 行。
    【什么时候用】侧栏删除会话。不删 order-api 进程。
    【谁调用】前端 delete。
    【里面调用】sandbox_manager.stop。
    """
    require_write(user)
    row = await _row(db, session_id)
    sandbox_manager.stop(row.id)
    from app.services.backup import BACKUP_ROOT

    shutil.rmtree(BACKUP_ROOT / session_id, ignore_errors=True)
    await db.delete(row)
    await db.commit()
    return {"ok": True}


@router.get("/{session_id}/messages", response_model=list[MessageOut])
async def list_messages(session_id: str, db: AsyncSession = Depends(get_db)) -> list[MessageOut]:
    """【功能】刷新后拉聊天气泡（含 kind=tool）。"""
    await _row(db, session_id)
    rows = (
        await db.execute(
            select(MessageRow)
            .where(MessageRow.session_id == session_id)
            .order_by(MessageRow.created_at.asc())
        )
    ).scalars()
    return [MessageOut(id=r.id, role=r.role, kind=r.kind or "", content=r.content) for r in rows]


@router.get("/{session_id}/audits")
async def list_audits(session_id: str, db: AsyncSession = Depends(get_db)) -> list[dict]:
    await _row(db, session_id)
    rows = (
        await db.execute(
            select(AuditRow)
            .where(AuditRow.session_id == session_id)
            .order_by(AuditRow.created_at.desc())
        )
    ).scalars()
    return [
        {
            "id": r.id,
            "tool_name": r.tool_name,
            "arguments": r.arguments[:2000],
            "decision": r.decision,
            "actor": r.actor,
        }
        for r in rows
    ]


@router.get("/{session_id}/tool-calls")
async def list_tool_calls(session_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    """【业务】值班台事后追查：这次会话 Agent 调了哪些工具，哈希链是否完整。"""
    await _row(db, session_id)
    rows = (
        await db.execute(
            select(ToolCallRow)
            .where(ToolCallRow.session_id == session_id)
            .order_by(ToolCallRow.created_at.asc(), ToolCallRow.id.asc())
        )
    ).scalars().all()
    return {
        "chain_ok": verify_chain(list(rows)),
        "items": [
            {
                "id": r.id,
                "tool_name": r.tool_name,
                "tool_call_id": r.tool_call_id,
                "agent_name": r.agent_name,
                "arguments": r.arguments[:2000],
                "output": r.output[:2000],
                "actor": r.actor,
                "state": r.state,
                "decision": r.decision,
                "prev_hash": r.prev_hash,
                "hash": r.hash,
                "created_at": r.created_at.isoformat() if r.created_at else "",
            }
            for r in rows
        ],
    }


@router.get("/{session_id}/tree")
async def file_tree(session_id: str, path: str = ".", db: AsyncSession = Depends(get_db)):
    """【功能】工作区一层目录，给左侧文件树。"""
    row = await _row(db, session_id)
    root = resolve_workspace(row.workspace_path)
    target = resolve_under_workspace(root, path)
    if not target.is_dir():
        raise HTTPException(400, "Not a directory")
    skip = {".git", "node_modules", "dist", "__pycache__"}
    parent = None
    if target != root:
        parent = "." if target.parent == root else target.parent.relative_to(root).as_posix()
    entries = []
    if parent is not None:
        entries.append({"name": "..", "path": parent, "dir": True})
    for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if child.name in skip or (child.name.startswith(".") and child.name not in {".gitignore"}):
            continue
        rel = child.relative_to(root).as_posix()
        entries.append({"name": child.name, "path": rel, "dir": child.is_dir()})
    return {"path": path.replace("\\", "/"), "parent": parent, "entries": entries}


def _entry_target(root: Path, rel: str) -> Path:
    target = resolve_under_workspace(root, rel)
    if target == root:
        raise HTTPException(400, "不能操作工作区根目录")
    name = target.name
    if name in {".", ".."} or "/" in rel.replace("\\", "/").split("/")[-1]:
        raise HTTPException(400, "非法名称")
    return target


@router.post("/{session_id}/mkdir")
async def mkdir(session_id: str, body: FsEntryIn, db: AsyncSession = Depends(get_db)):
    row = await _row(db, session_id)
    root = resolve_workspace(row.workspace_path)
    try:
        target = _entry_target(root, body.path)
    except WorkspaceError as exc:
        raise HTTPException(400, str(exc)) from exc
    if target.exists():
        raise HTTPException(409, "已存在同名文件或文件夹")
    target.mkdir(parents=True, exist_ok=False)
    return {"ok": True, "path": body.path, "dir": True}


@router.post("/{session_id}/touch")
async def touch_file(session_id: str, body: FsEntryIn, db: AsyncSession = Depends(get_db)):
    row = await _row(db, session_id)
    root = resolve_workspace(row.workspace_path)
    try:
        target = _entry_target(root, body.path)
    except WorkspaceError as exc:
        raise HTTPException(400, str(exc)) from exc
    if target.exists():
        raise HTTPException(409, "已存在同名文件或文件夹")
    target.parent.mkdir(parents=True, exist_ok=True)
    info = classify(target)
    if info["binary"]:
        target.write_bytes(b"")
    else:
        target.write_text(body.content, encoding="utf-8")
    return {"ok": True, "path": body.path, "dir": False}


@router.delete("/{session_id}/entry")
async def delete_entry(
    session_id: str,
    path: str,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_token),
):
    row = await _row(db, session_id)
    root = resolve_workspace(row.workspace_path)
    try:
        target = _entry_target(root, path)
    except WorkspaceError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not target.exists():
        raise HTTPException(404, "不存在")
    if target.is_file():
        from app.services.backup import snapshot_before_change

        await snapshot_before_change(
            session_id, root, target, "delete", user.username, row.model, ""
        )
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    return {"ok": True, "path": path}


@router.post("/{session_id}/rename")
async def rename_entry(session_id: str, body: FsRenameIn, db: AsyncSession = Depends(get_db)):
    row = await _row(db, session_id)
    root = resolve_workspace(row.workspace_path)
    try:
        src = _entry_target(root, body.path)
    except WorkspaceError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not src.exists():
        raise HTTPException(404, "不存在")
    new_name = body.name.strip()
    if new_name in {".", ".."} or any(ch in new_name for ch in '\\/:*?"<>|'):
        raise HTTPException(400, "非法名称")
    dest = src.parent / new_name
    try:
        dest = resolve_under_workspace(root, dest.relative_to(root).as_posix())
    except WorkspaceError as exc:
        raise HTTPException(400, str(exc)) from exc
    if dest == root:
        raise HTTPException(400, "不能操作工作区根目录")
    if dest.resolve() == src.resolve():
        return {"ok": True, "path": dest.relative_to(root).as_posix(), "dir": src.is_dir()}
    if dest.exists():
        raise HTTPException(409, "已存在同名文件或文件夹")
    src.rename(dest)
    rel = dest.relative_to(root).as_posix()
    return {"ok": True, "path": rel, "dir": dest.is_dir()}


@router.get("/{session_id}/file")
async def read_workspace_file(session_id: str, path: str, db: AsyncSession = Depends(get_db)):
    row = await _row(db, session_id)
    root = resolve_workspace(row.workspace_path)
    target = resolve_under_workspace(root, path)
    if not target.is_file():
        raise HTTPException(404, "File not found")

    def _load() -> dict:
        info = classify(target)
        size = target.stat().st_size
        if info["binary"]:
            return {"path": path, "content": "", "kind": info["kind"], "binary": True, "size": size}
        text = target.read_text(encoding="utf-8", errors="replace")
        if len(text) > 200_000:
            text = text[:200_000] + "\n\n[truncated]"
        return {"path": path, "content": text, "kind": info["kind"], "binary": False, "size": size}

    return await asyncio.to_thread(_load)


@router.get("/{session_id}/blob")
async def read_blob(session_id: str, path: str, db: AsyncSession = Depends(get_db)):
    row = await _row(db, session_id)
    root = resolve_workspace(row.workspace_path)
    target = resolve_under_workspace(root, path)
    if not target.is_file():
        raise HTTPException(404, "File not found")
    from fastapi.responses import FileResponse

    return FileResponse(target)


@router.put("/{session_id}/blob")
async def save_blob(session_id: str, path: str, request: Request, db: AsyncSession = Depends(get_db)):
    row = await _row(db, session_id)
    root = resolve_workspace(row.workspace_path)
    target = resolve_under_workspace(root, path)
    info = classify(target) if target.exists() else classify(Path(path))
    if info["kind"] in {"pdf", "image"}:
        raise HTTPException(400, "该格式不支持在工作台里保存")
    body = await request.body()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(body)
    return {"ok": True, "path": path, "size": len(body)}


@router.put("/{session_id}/file")
async def save_workspace_file(
    session_id: str,
    body: FileSaveIn,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_token),
):
    row = await _row(db, session_id)
    root = resolve_workspace(row.workspace_path)
    target = resolve_under_workspace(root, body.path)
    info = classify(target) if target.exists() else classify(Path(body.path))
    if info["kind"] in {"pdf", "image", "spreadsheet", "document", "media", "binary"}:
        raise HTTPException(400, "该格式请用对应预览保存，或不能在此编辑")
    from app.services.backup import snapshot_before_change

    await snapshot_before_change(
        session_id, root, target, "write", user.username, row.model, body.content
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body.content, encoding="utf-8")
    return {"ok": True, "path": body.path}


@router.post("/{session_id}/preview-write")
async def preview_write(
    session_id: str, body: WritePreviewIn, db: AsyncSession = Depends(get_db)
):
    """【功能】批准前给人看 unified diff。不写盘。"""
    row = await _row(db, session_id)
    root = resolve_workspace(row.workspace_path)
    target = resolve_under_workspace(root, body.path)
    old = ""
    exists = target.is_file()
    if exists:
        old = target.read_text(encoding="utf-8", errors="replace")
    diff = "".join(
        unified_diff(
            old.splitlines(keepends=True),
            body.content.splitlines(keepends=True),
            fromfile=f"a/{body.path}",
            tofile=f"b/{body.path}",
        )
    )
    return {"exists": exists, "diff": diff or "(no changes)"}


@router.get("/{session_id}/backups")
async def list_backups(session_id: str, db: AsyncSession = Depends(get_db)):
    """【功能】本会话写前快照列表，给回退弹窗。"""
    await _row(db, session_id)
    from app.services.backup import list_session_backups

    return {"items": await list_session_backups(session_id)}


@router.post("/{session_id}/backups/restore-all")
async def restore_all_backups(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_engineer),
):
    """【功能】整会话回退到各文件改前。"""
    row = await _row(db, session_id)
    root = resolve_workspace(row.workspace_path)
    from app.services.backup import restore_all

    try:
        done = await restore_all(session_id, root)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"ok": True, "restored": done, "actor": user.username}


@router.post("/{session_id}/backups/{backup_id}/restore")
async def restore_one_backup(
    session_id: str,
    backup_id: str,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_engineer),
):
    """【功能】按 backup_id 回退单个文件。"""
    row = await _row(db, session_id)
    root = resolve_workspace(row.workspace_path)
    from app.services.backup import restore_file

    try:
        msg = await restore_file(session_id, backup_id, root)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"ok": True, "restored": msg, "actor": user.username}
