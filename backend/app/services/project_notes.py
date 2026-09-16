"""
项目笔记与会话标题：把 AGENTS.md 读进系统提示，把首条用户话变成侧栏标题。

【在 Agent 里的位置】
    build_agent 拼 notes；send_message 用 with_open_file 告诉模型编辑器打开了谁；
    占位标题（仓库名 / New session / 值班 ·）会被 title_from_prompt 替换。
"""

from __future__ import annotations

from pathlib import Path

NOTE_FILES = ("AGENTS.md", "FORGE.md", ".forge.md")
NOTE_LIMIT = 6000


def load_project_notes(workspace: Path, limit: int = NOTE_LIMIT) -> str:
    """
    【功能】按优先级读第一份非空笔记，截到 6KB，避免撑爆系统提示。
    【输入】workspace、limit。输出：文本或空串。
    【调用】build_agent。
    """
    root = Path(workspace)
    for name in NOTE_FILES:
        path = root / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if text:
            return text[:limit]
    return ""


def with_open_file(user_text: str, open_path: str = "") -> str:
    """
    【功能】若前端传来当前打开文件，在用户话前面加 [当前编辑器打开：…]。
    【思路】拒 .. 和空路径，防止把越界路径写进 prompt。
    【输入】user_text、open_path。输出：可能加了前缀的字符串。
    """
    path = (open_path or "").replace("\\", "/").strip().lstrip("/")
    if not path or path in {".", ".."} or ".." in path.split("/"):
        return user_text
    return f"[当前编辑器打开：{path}]\n\n{user_text}"


def title_from_prompt(text: str, limit: int = 40) -> str:
    """【功能】压成一行，超长加省略号。给侧栏标题。"""
    one = " ".join((text or "").strip().split())
    if not one:
        return ""
    if len(one) > limit:
        return one[:limit].rstrip() + "…"
    return one


def is_placeholder_title(title: str, workspace_name: str = "") -> bool:
    """【功能】还是不是占位标题。是才允许用首条消息改名。"""
    t = (title or "").strip()
    if not t or t == "New session":
        return True
    if workspace_name and t == workspace_name:
        return True
    return t.startswith("值班 ·")
