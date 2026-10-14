"""
工具层硬拒绝：模型就算调用了也执行不了。对应简历「安全不是模型自觉」。

【在 Agent 里的位置】
    写文件 / bash / git 在真正改盘或 exec 之前先过本文件。
    auto_approve（无头评测）也走这里。和 HITL 是两道门：人可以批，代码仍可拒。

【功能对照】
    路径 jail、禁 C 盘、禁绝对路径、禁 .env/.git
    热修白名单只允许 bugs.py / app.py，拒 tests/、active_bug.txt
    禁 force push、reset --hard、rebase；bash 绕过同样拦

【调用关系】
    tools.write_file / delete_file → assert_write_path
    tools._safe_path / read_* → resolve_tool_path
    tools._git → assert_safe_git
    tools.bash → assert_safe_bash
    撞门脚本：backend/scripts/probe_safety_gates.py
"""

from __future__ import annotations

import re
from pathlib import Path

from app.services.workspace import WorkspaceError, is_c_drive, resolve_under_workspace

# Duty/hotfix on the monitored order-api: only these files may be written.
HOTFIX_WRITE_ALLOW = ("bugs.py", "app.py")

PROTECTED_NAMES = {".env", ".env.local", ".env.production"}


class SafetyError(WorkspaceError):
    """Raised when a tool must refuse. Message is shown to the model as the tool error."""


def _posix(rel: str) -> str:
    """【功能】把相对路径收成 posix，去掉 ./ 和盘符式前导斜杠，方便和白名单比。"""
    text = rel.replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text.lstrip("/")


def assert_safe_git(args: list[str]) -> None:
    """
    【功能】宿主 git 参数闸门。
    【思路】看参数集合和拼接串：--force / reset --hard / rebase / filter-branch / clean -f 直接拒绝。
    【输入】不含 git 本身的参数列表。输出：None 或抛 SafetyError（文案给模型看）。
    """
    lowered = {a.lower() for a in args}
    joined = " ".join(args).lower()
    if "--force" in lowered or "-f" in lowered or "--force-with-lease" in lowered:
        raise SafetyError("拒绝：禁止 force push。这是代码闸门，不是模型建议。")
    if "reset" in lowered and "--hard" in lowered:
        raise SafetyError("拒绝：禁止 git reset --hard。这是代码闸门，不是模型建议。")
    if "rebase" in lowered or "filter-branch" in lowered:
        raise SafetyError("拒绝：禁止 rebase / filter-branch。")
    if "clean" in lowered and ("-f" in lowered or "-fd" in joined or "-df" in joined):
        raise SafetyError("拒绝：禁止 git clean -f。")


def assert_safe_bash(command: str) -> None:
    """
    【功能】沙箱 bash 闸门。工作区是 rw 挂载，危险 git 必须在 exec 前拦。
    【思路】空命令拒；压缩空白后扫 reset --hard / force push / rebase / rm -rf / / C: / /mnt/c。
        带 ; ` && || 且夹着危险 git 也拒，防止「echo x && git reset --hard」。
    【输入】command 整串。输出：None 或 SafetyError。
    """
    raw = (command or "").strip()
    if not raw:
        raise SafetyError("拒绝：空命令。")
    text = raw.lower()
    if any(ch in raw for ch in [";", "`"]) or "&&" in raw or "||" in raw:
        # Allow simple && for short pipelines used in diagnosis, but not chained git writes.
        if re.search(r"\bgit\b", text) and re.search(
            r"reset\s+--hard|push\s+.*(-f|--force)|rebase|clean\s+-f", text
        ):
            raise SafetyError("拒绝：禁止在 bash 里组合执行危险 Git。请用专用 git 工具。")
    needles = (
        ("reset --hard", "拒绝：禁止通过 bash 执行 git reset --hard。"),
        ("push --force", "拒绝：禁止通过 bash 执行 force push。"),
        ("push -f", "拒绝：禁止通过 bash 执行 force push。"),
        ("--force-with-lease", "拒绝：禁止通过 bash 执行 force push。"),
        ("git rebase", "拒绝：禁止通过 bash 执行 rebase。"),
        ("filter-branch", "拒绝：禁止 filter-branch。"),
        ("rm -rf /", "拒绝：禁止删除根目录。"),
        ("rm -rf /*", "拒绝：禁止删除根目录。"),
        ("mkfs", "拒绝：禁止格式化磁盘。"),
        ("dd if=/dev/zero", "拒绝：禁止 dd 裸写磁盘。"),
        ("dd if=/dev/zero of=/dev/", "拒绝：禁止 dd 裸写磁盘。"),
        ("/mnt/c", "拒绝：禁止通过 bash 访问 Windows C 盘挂载。"),
        ("c:\\", "拒绝：禁止通过 bash 访问 C 盘。"),
        ("c:/", "拒绝：禁止通过 bash 访问 C 盘。"),
    )
    compact = re.sub(r"\s+", " ", text)
    for needle, msg in needles:
        if needle in compact or needle in text:
            raise SafetyError(msg)
    if re.search(r"\bgit\s+push\b.*\s-f\b", compact):
        raise SafetyError("拒绝：禁止通过 bash 执行 force push。")


def resolve_tool_path(workspace: Path, path: str) -> Path:
    """
    【功能】只读/写入共用的路径 jail。
    【思路】空路径拒；绝对路径或 X: 先看是不是 C 盘，再一律拒绝对路径；
        相对路径交给 resolve_under_workspace，逃出工作区就拒。
    【输入】workspace 根、模型给的 path。输出：已 resolve 的 Path。
    """
    raw = (path or "").strip()
    if not raw:
        raise SafetyError("拒绝：路径为空。")
    candidate = Path(raw)
    if candidate.is_absolute() or (len(raw) >= 2 and raw[1] == ":"):
        probe = candidate if candidate.is_absolute() else Path(raw)
        try:
            resolved = probe.resolve()
        except OSError:
            resolved = probe
        if is_c_drive(resolved) or raw.upper().startswith("C:"):
            raise SafetyError("拒绝：C 盘受保护，不能读写工作区外的绝对路径。请只用工作区内相对路径。")
        raise SafetyError("拒绝：不允许绝对路径。write_file / delete_file 的 path 必须是当前工作区内的相对路径。")
    try:
        target = resolve_under_workspace(workspace, raw)
    except WorkspaceError as exc:
        msg = str(exc)
        if "拒绝" in msg:
            raise SafetyError(msg) from exc
        raise SafetyError("拒绝：路径超出当前工作区，不能访问父目录或工作区外的文件。") from exc
    rel = target.relative_to(workspace.resolve()).as_posix()
    blocked = _protected_rel(rel)
    if blocked:
        raise SafetyError(blocked)
    return target


def _protected_rel(rel: str) -> str | None:
    """【功能】.env* 和 .git/ 不可写。命中返回拒绝文案，否则 None。"""
    posix = _posix(rel)
    name = Path(posix).name
    if name in PROTECTED_NAMES or posix.endswith("/.env"):
        return "拒绝：不能读写 .env。"
    if posix == ".git" or posix.startswith(".git/"):
        return "拒绝：不能修改 .git。"
    return None


def assert_write_path(workspace: Path, path: str, write_scope: str = "workspace") -> Path:
    """
    【功能】写/删文件的最终闸门。auto_approve 也会跑。
    【思路】先 jail，再拒 .env/.git；hotfix 再拒 active_bug.txt、tests/、以及非 bugs.py/app.py。
    【输入】workspace、path、write_scope（workspace|hotfix）。
    【输出】可写的 Path；否则 SafetyError。
    """
    target = resolve_tool_path(workspace, path)
    rel = target.relative_to(workspace.resolve()).as_posix()
    blocked = _protected_rel(rel)
    if blocked:
        raise SafetyError(blocked)
    scope = (write_scope or "workspace").strip() or "workspace"
    if scope == "hotfix":
        posix = _posix(rel)
        if posix == "active_bug.txt" or posix.endswith("/active_bug.txt"):
            raise SafetyError("拒绝：不能修改 active_bug.txt（那是当前题号，不是修复内容）。")
        if posix == "tests" or posix.startswith("tests/"):
            raise SafetyError("拒绝：热修模式不能改测试文件。修业务代码，不要改答案。")
        if posix not in HOTFIX_WRITE_ALLOW:
            allowed = "、".join(HOTFIX_WRITE_ALLOW)
            raise SafetyError(
                f"拒绝：热修模式只能改 {allowed}，不能改 {posix}。"
                "这是代码闸门。当前任务只修激活的那一个 bug。"
            )
    return target


def hotfix_commit_paths() -> list[str]:
    """【功能】热修 git_commit 允许 stage 的相对路径，与 HOTFIX_WRITE_ALLOW 一致。"""
    return list(HOTFIX_WRITE_ALLOW)
