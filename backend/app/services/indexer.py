"""
工作区切块索引：打开会话时重建，给 search_code 用 ILIKE 搜。

【在 Agent 里的位置】
    不是向量 RAG。embedding 列预留未填。模型也可改用 grep_files 扫盘。

【调用关系】
    sessions.create_session 后台任务 → index_workspace
    tools.search_code → search_chunks
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk import FileChunkRow
from app.services.workspace import workspace_key

_SKIP_DIRS = {".git", "node_modules", "dist", "__pycache__", ".venv", ".pnpm-store"}
_SKIP_SUFFIX = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".pdf",
    ".exe",
    ".dll",
    ".woff",
    ".woff2",
    ".zip",
}
_MAX_FILE_BYTES = 200_000
_MAX_FILES = 400
_MAX_CHUNKS = 2500
_CHUNK_LINES = 80


async def index_workspace(db: AsyncSession, root: Path) -> int:
    """
    【功能】删掉该工作区旧切块，按约 80 行一块重写，最多 400 文件 / 2500 块。
    【输入】db、工作区根。输出：写入块数。
    """
    key = workspace_key(root)
    await db.execute(delete(FileChunkRow).where(FileChunkRow.workspace_key == key))
    chunks: list[FileChunkRow] = []
    files_seen = 0
    for file in root.rglob("*"):
        if not file.is_file():
            continue
        if any(part in _SKIP_DIRS for part in file.parts):
            continue
        if file.suffix.lower() in _SKIP_SUFFIX:
            continue
        if files_seen >= _MAX_FILES or len(chunks) >= _MAX_CHUNKS:
            break
        try:
            if file.stat().st_size > _MAX_FILE_BYTES:
                continue
            text_body = file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        files_seen += 1
        rel = file.relative_to(root).as_posix()
        lines = text_body.splitlines()
        if not lines:
            continue
        for i in range(0, len(lines), _CHUNK_LINES):
            piece = "\n".join(lines[i : i + _CHUNK_LINES]).strip()
            if not piece:
                continue
            chunks.append(
                FileChunkRow(
                    workspace_key=key,
                    rel_path=rel,
                    chunk_index=i // _CHUNK_LINES,
                    content=piece[:8000],
                )
            )
            if len(chunks) >= _MAX_CHUNKS:
                break
    db.add_all(chunks)
    await db.commit()
    return len(chunks)


async def search_chunks(db: AsyncSession, root: Path, query: str, limit: int = 20) -> str:
    """
    【功能】对本工作区 content ILIKE %query%。未命中提示重建索引或改用 grep。
    【输入】db、root、query、limit。输出：给模型看的多段文本。
    """
    key = workspace_key(root)
    q = f"%{query[:120]}%"
    result = await db.execute(
        text(
            """
            SELECT rel_path, chunk_index, left(content, 600) AS snippet
            FROM file_chunks
            WHERE workspace_key = :key AND content ILIKE :q
            ORDER BY rel_path, chunk_index
            LIMIT :limit
            """
        ),
        {"key": key, "q": q, "limit": limit},
    )
    rows = result.mappings().all()
    if not rows:
        return "No indexed matches. Try grep_files or re-open the workspace to rebuild the index."
    lines = [f"{r['rel_path']}#{r['chunk_index']}:\n{r['snippet']}" for r in rows]
    return "\n\n".join(lines)
