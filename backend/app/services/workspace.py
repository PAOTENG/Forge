"""
工作区选择与路径解析：目录浏览器、禁 C 盘、Docker 挂载路径。

【在 Agent 里的位置】
    开会话时 resolve_workspace 定根；工具路径再经 safety → resolve_under_workspace。
    左侧点仓库名换目录走 system/browse + list_allowed_drives。

【调用关系】
    sessions.create_session、chat、safety.resolve_tool_path、sandbox.docker_bind、indexer.workspace_key。
"""

from __future__ import annotations

import os
import string
import tempfile
from pathlib import Path


class WorkspaceError(ValueError):
    """工作区不合法（C 盘、不可写、越界）。路由转成 HTTP 400。"""

    pass


def is_c_drive(path: Path) -> bool:
    """【功能】Windows 上是否落在 C:。输入 Path，输出 bool。"""
    drive = path.resolve().drive
    return bool(drive) and drive.upper() == "C:"


def needs_elevated_access(path: Path) -> bool:
    """
    【功能】探测当前用户能否在该目录写文件（管理员 ACL / 只读盘）。
    【思路】先 os.access，再 mkstemp 真写一笔再删。写失败视为需要提权，不能当工作区。
    【输入】目录 Path。输出：True=不能选。
    """
    if not os.access(path, os.W_OK | os.X_OK):
        return True
    probe_path = None
    try:
        fd, name = tempfile.mkstemp(prefix=".forge-", dir=str(path))
        probe_path = name
        os.close(fd)
        os.unlink(name)
        return False
    except OSError:
        if probe_path:
            try:
                os.unlink(probe_path)
            except OSError:
                pass
        return True


def assert_workspace_allowed(path: Path) -> None:
    """【功能】选工作区时的闸门：拒 C 盘、拒不可写。输入 Path，失败抛 WorkspaceError。"""
    if is_c_drive(path):
        raise WorkspaceError("C 盘受保护，不能作为工作区")
    if needs_elevated_access(path):
        raise WorkspaceError("该目录需要管理员权限或当前用户无法写入，不能选择")


def list_allowed_drives() -> list[dict]:
    """
    【功能】目录浏览器顶层：Windows 列出非 C 盘；其它系统给 /。
    【输出】[{path, name}, ...]。调用：system.drives。
    """
    drives: list[dict] = []
    if os.name == "nt":
        bitmask = 0
        try:
            import ctypes

            bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        except Exception:
            bitmask = 0
        letters = [c for i, c in enumerate(string.ascii_uppercase) if bitmask & (1 << i)]
        if not letters:
            letters = [p.drive.replace(":", "") for p in Path("D:/").parents]
        for letter in letters:
            if letter.upper() == "C":
                continue
            root = Path(f"{letter}:/")
            if root.exists():
                drives.append({"path": str(root.resolve()), "name": f"{letter}:\\"})
    else:
        drives.append({"path": "/", "name": "/"})
    return drives


def browse_dir(raw: str) -> dict:
    """
    【功能】列某一层子目录（只列文件夹），并标 writable/blocked。
    【输入】raw 用户点的路径。输出：path/parent/writable/blocked/entries。
    【调用】GET /api/system/browse。
    """
    path = Path(raw).expanduser().resolve()
    if not path.exists() or not path.is_dir():
        raise WorkspaceError(f"目录不存在: {path}")
    if is_c_drive(path):
        raise WorkspaceError("C 盘受保护，不能浏览")
    skip = {".git", "node_modules", "dist", "__pycache__", "System Volume Information"}
    entries = []
    parent = str(path.parent.resolve()) if path.parent != path else None
    if parent and is_c_drive(Path(parent)):
        parent = None
    try:
        children = list(path.iterdir())
    except OSError as exc:
        raise WorkspaceError("没有权限列出该目录（可能需要管理员权限）") from exc
    for child in sorted(children, key=lambda p: (not p.is_dir(), p.name.lower())):
        if child.name in skip or (child.name.startswith(".") and child.name not in {".gitignore"}):
            continue
        if not child.is_dir():
            continue
        entries.append({"name": child.name, "path": str(child.resolve()), "dir": True})
    writable = not needs_elevated_access(path)
    return {
        "path": str(path),
        "parent": parent,
        "writable": writable,
        "blocked": is_c_drive(path) or not writable,
        "entries": entries,
    }


def resolve_workspace(root: str) -> Path:
    """
    【功能】开会话时把字符串收成合法工作区根。不存在或被禁则 WorkspaceError。
    【输入】用户/告警配置给的路径。输出：resolve 后的 Path。
    """
    path = Path(root).expanduser().resolve()
    if not path.exists() or not path.is_dir():
        raise WorkspaceError(f"Workspace does not exist: {path}")
    assert_workspace_allowed(path)
    return path


def resolve_under_workspace(root: Path, rel: str) -> Path:
    """
    【功能】相对路径必须落在 root 内，防 ../ 逃逸。
    【输入】root、rel。输出：candidate.resolve()；越界抛 WorkspaceError。
    """
    raw = Path(rel)
    candidate = (root / rel).resolve() if not raw.is_absolute() else raw.resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise WorkspaceError("拒绝：路径超出当前工作区（Path escapes the selected workspace）") from exc
    return candidate


def docker_bind(host_path: Path) -> str:
    """
    【业务】把值班机上的工作区路径交给 Docker 做挂载。Windows 是 D:\\project\\...，
            Docker 要 D:/project/...，否则 volume 挂不上，容器里 /workspace 是空的。
    【什么时候用】ensure() 第一次创建容器、写 volumes 字典时。
    【谁调用】sandbox.SandboxManager.ensure。
    【参数】host_path：已 resolve 的工作区根，热修一般是 targets/order-api。
    【是否主线】沙箱帮手。不挂载则 bash 改的不是你正在修的那份代码。
    """
    return str(host_path.resolve()).replace("\\", "/")


def workspace_key(root: Path) -> str:
    """【功能】工作区路径哈希，作 file_chunks 分区键。换目录互不覆盖索引。"""
    import hashlib

    return hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:32]
