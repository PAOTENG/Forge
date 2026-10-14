"""
Agent 的全部工具：模型只能通过这里碰工作区和业务进程。

【在 Agent 里的位置】
    OpenAI Agents SDK 的工具循环：模型输出 function_call → SDK 调本文件里的
    @function_tool → 把返回值当 function_call_output 再喂给模型。
    build_agent() 把 all_tools() 整表挂到 Agent 上。没有 LangGraph 节点。

【功能对照】
    只读诊断：read_file / list_dir / grep_files / glob_files / search_code
              + git_status / git_diff / git_log / git_branch
    8 类变更（needs_approval=_needs_hitl，且仅 build 模式 is_enabled=_is_build）：
              write_file / delete_file / bash / git_checkout / git_commit
              / git_push / run_tests / restart_service

【调用关系】
    被 agent_runtime.build_agent 引用。
    工具内部再调：safety 闸门、sandbox_manager.exec（bash / 普通仓测试）、supervisor、backup、indexer。
    沙箱不是本文件创建的：点排查/新建会话时路由已经 ensure；exec 里会再确保还活着。

【上下文】
    每个工具第一参是 RunContextWrapper[ForgeContext]，里面有工作区、模式、
    是否 auto_approve、write_scope（hotfix 时只能写 bugs.py/app.py）。
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from agents import RunContextWrapper, function_tool

from app.db.session import get_session_factory
from app.services.indexer import search_chunks
from app.services.sandbox import sandbox_manager
from app.services.safety import (
    assert_safe_bash,
    assert_write_path,
    hotfix_commit_paths,
    resolve_tool_path,
)


@dataclass
class ForgeContext:
    """
    【功能】一次 Agent run 的侧信道：工具能读到会话是谁、在哪个盘、能不能自动批。
    【思路】不把这些塞进模型可见的 messages，避免泄漏路径策略；SDK 通过 ctx.context 传入。
    【字段】
        session_id：用来挂沙箱容器名、写备份、落库。
        workspace：路径 jail 的根。
        mode：plan / build。
        actor：登录名，git commit 的 user.name。
        auto_approve：评测/管理员可跳过 HITL，但 safety 闸门仍执行。
        allow_git_push：无头评测 False，git_push 直接返回 skipped。
        write_scope：workspace 普通仓；hotfix 监控仓只允许 bugs.py/app.py。
        original_intent：本轮用户原话，给 Guardrail 做意图一致性，不进提示词。
    【谁创建】agent_runtime.make_forge_context。
    """

    session_id: str
    workspace: Path
    mode: str
    actor: str = "local"
    model: str = ""
    role: str = ""
    auto_approve: bool = False
    allow_git_push: bool = True
    write_scope: str = "workspace"
    original_intent: str = ""


def _safe_path(ctx: RunContextWrapper[ForgeContext], path: str) -> Path:
    """
    【功能】把模型给的相对路径收成工作区内的绝对 Path，越界/C 盘直接 SafetyError。
    【思路】所有读工具都先走这里，和写工具的 assert_write_path 共用 resolve_tool_path。
    【输入】ctx：当前 run；path：模型传入的字符串。
    【输出】已 resolve 且落在 workspace 内的 Path。
    """
    return resolve_tool_path(ctx.context.workspace, path)


def _read_file_sync(target: Path) -> str:
    """
    【功能】同步读文本。超 80KB 截断，避免一次把整个仓塞进上下文。
    【输入】target：已通过 jail 的文件路径。
    【输出】文件内容或 FileNotFoundError。
    【调用】read_file 用 asyncio.to_thread 包一层，避免堵事件循环。
    """
    if not target.is_file():
        raise FileNotFoundError(str(target))
    data = target.read_text(encoding="utf-8", errors="replace")
    if len(data) > 80_000:
        return data[:80_000] + "\n\n[truncated]"
    return data


@function_tool
async def read_file(ctx: RunContextWrapper[ForgeContext], path: str) -> str:
    """
    【功能】诊断：读工作区内一个 UTF-8 文件。值班热修时模型先读 bugs.py / app.py / 堆栈。
    【思路】路径必须相对工作区；绝对路径和 C: 被 safety 拒绝。大文件截断。
    【输入】path：相对路径，如 bugs.py。
    【输出】文件文本（可能带 [truncated]）。
    【调用】模型 function_call；内部 → _safe_path → _read_file_sync。
    """
    return await asyncio.to_thread(_read_file_sync, _safe_path(ctx, path))


def _list_dir_sync(target: Path) -> str:
    """
    【功能】同步列一层目录。隐藏点文件（保留 .gitignore / .env.example），最多 400 行。
    【输入】target：已 jail 的目录。输出：每行一个名字，目录带 /。
    """
    if not target.is_dir():
        raise NotADirectoryError(str(target))
    lines: list[str] = []
    for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if child.name.startswith(".") and child.name not in {".env.example", ".gitignore"}:
            continue
        mark = "/" if child.is_dir() else ""
        lines.append(f"{child.name}{mark}")
    return "\n".join(lines[:400]) or "(empty)"


@function_tool
async def list_dir(ctx: RunContextWrapper[ForgeContext], path: str = ".") -> str:
    """
    【功能】诊断：看工作区某一层有哪些文件。模型用来摸清 order-api 结构。
    【输入】path：相对目录，默认根。输出：名字列表文本。
    【调用】模型 → _safe_path → _list_dir_sync。
    """
    return await asyncio.to_thread(_list_dir_sync, _safe_path(ctx, path))


def _grep_sync(workspace: Path, root: Path, pattern: str) -> str:
    """
    【功能】同步扫文本文件做子串匹配。
    【思路】rglob 跳过常见垃圾目录和图片后缀；命中格式 rel:lineno:line，满 80 条提前停。
    【输入】workspace 用于算相对路径；root 搜索起点；pattern 子串。
    【输出】多行文本或 No matches.
    """
    skip = {".git", "node_modules", "dist", "__pycache__", ".venv"}
    hits: list[str] = []
    files = [root] if root.is_file() else root.rglob("*")
    for file in files:
        if not file.is_file():
            continue
        if any(part in skip for part in file.parts):
            continue
        if file.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".exe", ".dll"}:
            continue
        if file.name in {".env", ".env.local", ".env.production"} or file.name.endswith(".env"):
            continue
        try:
            text = file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        rel = file.relative_to(workspace)
        for i, line in enumerate(text.splitlines(), 1):
            if pattern.lower() in line.lower():
                hits.append(f"{rel}:{i}:{line[:240]}")
                if len(hits) >= 80:
                    return "\n".join(hits)
    return "\n".join(hits) or "No matches."


@function_tool
async def grep_files(ctx: RunContextWrapper[ForgeContext], pattern: str, path: str = ".") -> str:
    """
    【功能】诊断：工作区子串搜索（忽略大小写），返回 path:line:text，最多 80 条。
    【思路】跳过 .git/node_modules 和图片二进制；不用系统 grep，避免沙箱/Windows 差异。
    【输入】pattern：要找的字；path：搜索根，默认整个工作区。
    【输出】命中行或 “No matches.”。
    【调用】模型；实现在 _grep_sync。
    """
    root = _safe_path(ctx, path)
    return await asyncio.to_thread(_grep_sync, ctx.context.workspace, root, pattern)


def _glob_sync(root: Path, pattern: str) -> str:
    """
    【功能】同步 glob。对相对路径和文件名都做 fnmatch。
    【输入】root=工作区；pattern 如 **/*.py。输出：最多 200 条相对路径。
    """
    skip = {".git", "node_modules", "dist", "__pycache__"}
    matches: list[str] = []
    for file in root.rglob("*"):
        if not file.is_file():
            continue
        if any(part in skip for part in file.parts):
            continue
        if file.name in {".env", ".env.local", ".env.production"}:
            continue
        rel = file.relative_to(root).as_posix()
        if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(file.name, pattern):
            matches.append(rel)
            if len(matches) >= 200:
                break
    return "\n".join(matches) or "No files matched."


@function_tool
async def glob_files(ctx: RunContextWrapper[ForgeContext], pattern: str) -> str:
    """
    【功能】诊断：按 glob 找文件（如 **/*.py），最多 200 个。
    【输入】pattern：fnmatch 模式。输出：相对路径列表或 No files matched.
    【调用】模型 → _glob_sync(workspace, pattern)。
    """
    return await asyncio.to_thread(_glob_sync, ctx.context.workspace, pattern)


@function_tool
async def search_code(ctx: RunContextWrapper[ForgeContext], query: str) -> str:
    """
    【功能】诊断：搜打开会话时建好的 Postgres 切块（ILIKE），不是实时扫盘。
    【思路】大仓时比 grep 便宜；索引过期就提示用 grep_files 或重开会话。
    【输入】query：关键词。输出：rel_path#chunk + 摘要，或未命中说明。
    【调用】indexer.search_chunks。
    """
    factory = get_session_factory()
    async with factory() as db:
        return await search_chunks(db, ctx.context.workspace, query)


def assert_safe_git(args: list[str]) -> None:
    """
    【功能】宿主 git 入口的闸门转发：禁 force / reset --hard / rebase。
    【输入】args：git 子命令参数列表（不含 git 本身）。
    【输出】None；危险则抛 SafetyError，模型会看到「拒绝：…」。
    【调用】_git 在 subprocess 之前。
    """
    from app.services.safety import assert_safe_git as _assert

    _assert(args)


def _git(ctx: RunContextWrapper[ForgeContext], args: list[str], timeout: int = 30) -> str:
    """
    【功能】在工作区 cwd 跑宿主 git，关掉交互提示（GIT_TERMINAL_PROMPT=0）。
    【思路】先 assert_safe_git；capture 合并 stdout/stderr；非 0 也把输出回给模型而不是抛异常。
    【输入】args 如 ["status","--short"]；timeout 秒。
    【输出】文本或 [exit n]\\n...
    """
    assert_safe_git(args)
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    result = subprocess.run(
        ["git", *args],
        cwd=ctx.context.workspace,
        capture_output=True,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    out = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0:
        return f"[exit {result.returncode}]\n{out.strip()}".strip()
    return out.strip() or "(no output)"


async def _git_async(ctx: RunContextWrapper[ForgeContext], args: list[str], timeout: int = 30) -> str:
    """
    【功能】把同步 _git 丢到线程池，避免堵 FastAPI 事件循环。
    【输入】与 _git 相同。输出：stdout+stderr 或 [exit n]。
    """
    return await asyncio.to_thread(_git, ctx, args, timeout)


@function_tool
async def git_status(ctx: RunContextWrapper[ForgeContext]) -> str:
    """
    【功能】只读：宿主 git status。热修前后模型用来看改了哪些文件。
    【输入】无（工作区来自 ctx）。输出：短状态文本。
    【注意】走宿主而不是沙箱，因为沙箱 network=none 且没有值班机凭据。
    """
    return await _git_async(ctx, ["status", "--short", "--branch"])


@function_tool
async def git_diff(ctx: RunContextWrapper[ForgeContext], path: str = "") -> str:
    """
    【功能】只读：git diff，可选单个文件。截断 12KB 以免撑爆上下文。
    【输入】path：相对路径，空则全部。输出：diff 文本。
    """
    args = ["diff", "--", path] if path.strip() else ["diff"]
    text = await _git_async(ctx, args)
    return text[:12_000]


def _is_build(ctx: RunContextWrapper[ForgeContext], _agent) -> bool:
    """
    【功能】SDK 回调：plan 模式隐藏全部变更工具，模型只能读和出编号计划。
    【输入】ctx.context.mode。输出：True=build 可见。
    【调用】写在 @function_tool(is_enabled=_is_build) 上。
    """
    return ctx.context.mode != "plan"


def _needs_hitl(ctx: RunContextWrapper[ForgeContext], _args: dict, _call_id: str) -> bool:
    """
    【功能】SDK 回调：变更工具要不要暂停等人批。
    【思路】评测设 auto_approve=True 则不打断；管理员且 forge_admin_auto_approve 也不打断。
            闸门仍在工具函数里跑，自动批不等于能改测试或 C 盘。
    【输入】ctx；_args/_call_id 本实现不用。
    【输出】True=SDK 产生 interruption，Runtime 发 approval_needed。
    """
    context = ctx.context
    if context.auto_approve:
        return False
    if context.role == "admin":
        from app.core.config import get_settings

        if get_settings().forge_admin_auto_approve:
            return False
    return True


@function_tool
async def git_log(ctx: RunContextWrapper[ForgeContext], n: int = 12) -> str:
    """
    【功能】只读：最近 n 条 oneline log（夹在 1～30）。
    【输入】n。输出：提交列表。
    """
    count = max(1, min(int(n), 30))
    return await _git_async(ctx, ["log", f"-{count}", "--oneline", "--decorate"])


@function_tool
async def git_branch(ctx: RunContextWrapper[ForgeContext]) -> str:
    """
    【功能】只读：本地分支和跟踪关系。
    【输入】无。输出：git branch -vv。
    """
    return await _git_async(ctx, ["branch", "-vv"])


@function_tool(needs_approval=_needs_hitl, is_enabled=_is_build)
async def git_checkout(ctx: RunContextWrapper[ForgeContext], ref: str) -> str:
    """
    【功能】变更：切到已有分支或 checkout -b 新建。必须 HITL。
    【思路】拒绝以 - 开头或含 shell 元字符的 ref，防止参数注入。不存在则建分支。
    【输入】ref：分支名。输出：git 命令文本结果。
    """
    name = (ref or "").strip()
    if not name or name.startswith("-") or any(ch in name for ch in " \\;&|"):
        raise ValueError("非法分支名")
    existing = await _git_async(ctx, ["rev-parse", "--verify", name])
    if existing.startswith("[exit"):
        return await _git_async(ctx, ["checkout", "-b", name])
    return await _git_async(ctx, ["checkout", name])


@function_tool(needs_approval=_needs_hitl, is_enabled=_is_build)
async def git_commit(ctx: RunContextWrapper[ForgeContext], message: str, paths: str = "") -> str:
    """
    【功能】变更：宿主 git add + commit。值班热修闭环的「提交」一步。必须 HITL。
    【思路】
        不提交 .env；热修 write_scope=hotfix 时只能 stage bugs.py/app.py，
        没给 paths 就强制只加白名单，防止把测试或题号一起提交。
        author 用当前登录名，不把模型名写进 git。
    【输入】message：至少 3 字；paths：逗号分隔相对路径，可空。
    【输出】commit 命令输出。
    【调用】_git_async；闸门 assert_safe_git。
    """
    msg = (message or "").strip()
    if len(msg) < 3:
        raise ValueError("提交说明太短")
    from app.services.dlp import assert_safe_to_persist

    assert_safe_to_persist(msg, where="git_commit message")
    items = [p.strip().replace("\\", "/") for p in paths.split(",") if p.strip()]
    blocked = {".env", ".env.local", ".env.production"}
    if any(p in blocked or p.endswith("/.env") for p in items):
        raise ValueError("不能提交 .env")
    if ctx.context.write_scope == "hotfix":
        allowed = set(hotfix_commit_paths())
        if items:
            bad = [p for p in items if p not in allowed]
            if bad:
                raise ValueError(f"拒绝：热修模式只能提交 {', '.join(sorted(allowed))}，不能提交 {', '.join(bad)}")
        else:
            items = list(allowed)
    if items:
        await _git_async(ctx, ["add", "--", *items])
    else:
        await _git_async(ctx, ["add", "-u"])
    actor = ctx.context.actor or "forge"
    return await _git_async(
        ctx,
        [
            "-c",
            f"user.name={actor}",
            "-c",
            f"user.email={actor}@forge.local",
            "commit",
            "-m",
            msg,
        ],
    )


@function_tool(needs_approval=_needs_hitl, is_enabled=_is_build)
async def git_push(ctx: RunContextWrapper[ForgeContext], remote: str = "origin", branch: str = "") -> str:
    """
    【功能】变更：宿主 git push，用值班机已有凭据。必须 HITL。禁止 force（闸门拦）。
    【思路】评测 allow_git_push=False 时直接返回 skipped，避免偷 push 污染远端。
        成功则 mark_session_pushed，备份 TTL 从现在再计 7 天。
    【输入】remote 默认 origin；branch 可空（推当前分支）。
    【输出】push 文本或 “eval mode: git_push skipped”。
    """
    if not ctx.context.allow_git_push:
        return "eval mode: git_push skipped"
    rem = (remote or "origin").strip() or "origin"
    if rem.startswith("-"):
        raise ValueError("非法 remote")
    br = (branch or "").strip()
    if br.startswith("-"):
        raise ValueError("非法分支")
    args = ["push", rem] + ([br] if br else [])
    out = await _git_async(ctx, args, timeout=120)
    if not out.startswith("[exit"):
        from app.services.backup import mark_session_pushed

        await mark_session_pushed(ctx.context.session_id)
    return out


def default_test_command(workspace: Path) -> str:
    """
    【功能】按仓库形态猜一条最小测试命令。
    【思路】有 backend/tests → pytest backend/tests；有 tests/ → pytest tests；有 package.json → npm test。
    【输入】workspace。输出：命令字符串，给 run_tests 在没指定 command 时用。
    """
    if (workspace / "backend" / "tests").is_dir():
        return "python -m pytest -q backend/tests"
    if (workspace / "tests").is_dir():
        return "python -m pytest -q tests"
    if (workspace / "package.json").is_file():
        return "npm test --silent"
    return "python -m pytest -q"


@function_tool(needs_approval=_needs_hitl, is_enabled=_is_build)
async def run_tests(ctx: RunContextWrapper[ForgeContext], command: str = "") -> str:
    """
    【功能】变更：跑测试。热修闭环要求「先测后重启」。必须 HITL。
    【思路】order-api 有 .forge-host-tests：测宿主盘上的代码，不进沙箱。
            其它仓走 sandbox_manager.exec（内部会 ensure）。命令禁 ;|` 和 sudo。
    【输入】command：可空，空则 default_test_command。
    【输出】pytest/npm 文本，失败带 [exit n]。
    【调用】supervisor.run_host_tests 或 sandbox.exec。
    """
    marker = ctx.context.workspace / ".forge-host-tests"
    if marker.is_file():
        from app.services.supervisor import run_host_tests

        return await asyncio.to_thread(run_host_tests)
    cmd = (command or "").strip() or default_test_command(ctx.context.workspace)
    if any(ch in cmd for ch in ";|`") or "sudo" in cmd:
        raise ValueError("测试命令不安全")
    return await asyncio.to_thread(
        sandbox_manager.exec, ctx.context.session_id, ctx.context.workspace, cmd
    )


@function_tool(needs_approval=_needs_hitl, is_enabled=_is_build)
async def restart_service(ctx: RunContextWrapper[ForgeContext]) -> str:
    """
    【功能】变更：重启被监控的 order-api，让热修进活进程。必须 HITL。
    【思路】工作区必须是 target_root，防止模型在随便一个仓上重启业务。
        重启后 sleep 1.2s 再 snapshot，把 canary 是否变绿回给模型。
        系统提示禁止用重启当「试一试」。
    【输入】无。输出：restart 结果 + running/pid/health/canary_ok。
    【调用】supervisor.restart_target / snapshot。
    """
    from app.services.supervisor import is_target_workspace, restart_target, snapshot

    if not is_target_workspace(ctx.context.workspace):
        raise ValueError("当前工作区不是被监控的业务项目，不能重启")
    result = await asyncio.to_thread(restart_target)
    await asyncio.sleep(1.2)
    state = await asyncio.to_thread(snapshot)
    health = state.get("health") or {}
    return (
        f"restart={result}\n"
        f"running={state.get('running')} pid={state.get('pid')} "
        f"health={health.get('status_code')} canary_ok={state.get('canary', {}).get('ok')}"
    )


@function_tool(needs_approval=_needs_hitl, is_enabled=_is_build)
async def write_file(ctx: RunContextWrapper[ForgeContext], path: str, content: str) -> str:
    """
    【功能】变更：写/覆盖工作区文本文件。热修最小补丁的主入口。必须 HITL。
    【思路】先 assert_write_path（jail + 热修白名单），再 snapshot_before_change，最后 write_text。
        闸门在 auto_approve 时也跑。
    【输入】path 相对路径；content 全文。
    【输出】Wrote rel (n chars)。拒绝则 SafetyError 文本回给模型。
    【调用】safety.assert_write_path、backup.snapshot_before_change、dlp.assert_safe_to_persist。
    """
    from app.services.backup import snapshot_before_change
    from app.services.dlp import assert_safe_to_persist

    assert_safe_to_persist(content, where="write_file")
    target = assert_write_path(ctx.context.workspace, path, ctx.context.write_scope)
    await snapshot_before_change(
        ctx.context.session_id,
        ctx.context.workspace,
        target,
        "write",
        ctx.context.actor,
        ctx.context.model,
        content,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"Wrote {target.relative_to(ctx.context.workspace).as_posix()} ({len(content)} chars)"


@function_tool(needs_approval=_needs_hitl, is_enabled=_is_build)
async def delete_file(ctx: RunContextWrapper[ForgeContext], path: str) -> str:
    """
    【功能】变更：删一个文件（不删目录）。闸门与 write_file 相同。必须 HITL。
    【思路】先备份再 unlink，回退时按 existed 决定是还原还是删新建文件。
    【输入】path。输出：Deleted rel。
    """
    from app.services.backup import snapshot_before_change

    target = assert_write_path(ctx.context.workspace, path, ctx.context.write_scope)
    if not target.is_file():
        raise FileNotFoundError(str(target))
    await snapshot_before_change(
        ctx.context.session_id,
        ctx.context.workspace,
        target,
        "delete",
        ctx.context.actor,
        ctx.context.model,
        "",
    )
    rel = target.relative_to(ctx.context.workspace).as_posix()
    target.unlink()
    return f"Deleted {rel}"


@function_tool(needs_approval=_needs_hitl, is_enabled=_is_build)
async def bash(ctx: RunContextWrapper[ForgeContext], command: str) -> str:
    """
    【功能】变更：在会话沙箱里跑一条 shell。必须 HITL。
    【思路】先 assert_safe_bash 拦危险 git / C 盘 / rm -rf；再 docker exec。
            人点允许之前 SDK 不会进到本函数。容器应已在 investigate/开会话时 ensure 过。
            沙箱默认没网、2g、256 pid。宿主 git 请用专用 git_* 工具。
    【什么时候用】模型在 build 模式要跑一条 shell，且你已批准。
    【谁调用】SDK 工具循环（all_tools 挂上的 bash）。
    【输入】command：单条命令。输出：stdout/stderr 或 [exit n]。
    【调用】sandbox_manager.exec。
    """
    from app.services.dlp import assert_no_egress, assert_safe_to_persist

    assert_safe_bash(command)
    assert_no_egress(command)
    assert_safe_to_persist(command, where="bash")
    return await asyncio.to_thread(
        sandbox_manager.exec, ctx.context.session_id, ctx.context.workspace, command
    )


@function_tool
async def a2a_roomcheck(
    ctx: RunContextWrapper[ForgeContext],
    skill: str = "canary",
    room_no: str = "",
    extra: str = "",
) -> str:
    """
    通过官方 A2A 向 RoomCheck 业务 Agent 委派任务。skill=canary 复检空茶几金丝雀；
    skill=inspect 走客房验收。禁止 curl RoomCheck 私有 HTTP。缺茶杯是业务结果，不是热修。
    """
    from app.a2a.client import roomcheck_skill
    from app.services.dlp import assert_safe_to_persist, redact_text

    name = "room-canary" if skill.strip().lower() in {"canary", "room-canary", ""} else "room-inspect"
    payload = {}
    if room_no.strip():
        payload["room_no"] = room_no.strip()
    if extra.strip():
        assert_safe_to_persist(extra, where="a2a_roomcheck extra")
        payload["extra"] = extra.strip()
    result = await roomcheck_skill(name, payload)
    return redact_text(json.dumps(result, ensure_ascii=False)[:4000])


@function_tool
async def a2a_kb(
    ctx: RunContextWrapper[ForgeContext],
    skill: str = "ask",
    question: str = "",
) -> str:
    """
    通过官方 A2A 调用知识库问答 Agent。skill=ask 时传入 question；
    skill=canary 只做对端自检。禁止 curl 对端私有 HTTP。
    """
    from app.a2a.client import kb_skill
    from app.services.dlp import assert_safe_to_persist, redact_text

    name = (skill or "ask").strip().lower()
    if name in {"canary", "kb-canary"}:
        result = await kb_skill("kb-canary")
    else:
        text = question.strip()
        if text:
            assert_safe_to_persist(text, where="a2a_kb question")
        result = await kb_skill("kb-ask", {"question": text})
    return redact_text(json.dumps(result, ensure_ascii=False)[:4000])


def readonly_review_tools():
    """
    【功能】只读评审子 Agent 的工具面：能看代码和 diff，不能改盘。
    【思路】和 all_tools 共用同一批 @function_tool，闸门仍走 ForgeContext。
    【调用】agent_runtime.build_agent 组 hotfix_reviewer。
    """
    return [
        read_file,
        list_dir,
        grep_files,
        glob_files,
        git_status,
        git_diff,
    ]


def all_tools():
    """
    【功能】返回 SDK 要挂上的工具对象（顺序即模型可见列表）。
    【思路】只读在前、变更在后；build_agent 一次全挂，由 is_enabled/needs_approval 动态裁。
            评审子 Agent 另走 readonly_review_tools，再 as_tool 挂到主管上。
    【输入】无。输出：function_tool 列表。
    【调用】agent_runtime.build_agent。
    """
    return [
        read_file,
        list_dir,
        grep_files,
        glob_files,
        search_code,
        git_status,
        git_diff,
        git_log,
        git_branch,
        git_checkout,
        git_commit,
        git_push,
        run_tests,
        restart_service,
        write_file,
        delete_file,
        bash,
        a2a_roomcheck,
        a2a_kb,
    ]


def _install_guardrails() -> None:
    from app.services.guardrails import attach_tool_guardrails

    attach_tool_guardrails(all_tools())


_install_guardrails()
