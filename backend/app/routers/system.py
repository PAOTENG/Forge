"""
工作区目录浏览器：列出非 C 盘、浏览子目录。开会话前选仓用。
"""

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.security import require_token
from app.services.workspace import WorkspaceError, browse_dir, list_allowed_drives

router = APIRouter(prefix="/system", tags=["system"], dependencies=[Depends(require_token)])


@router.get("/drives")
async def drives() -> dict:
    """【功能】GET /api/system/drives。输出非 C 盘列表。"""
    return {"drives": list_allowed_drives()}


@router.get("/browse")
async def browse(path: str = Query(..., min_length=1)) -> dict:
    """【功能】GET /api/system/browse?path=。输出子目录和是否可写。"""
    try:
        return browse_dir(path)
    except WorkspaceError as exc:
        raise HTTPException(400, str(exc)) from exc
