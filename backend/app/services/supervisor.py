"""
被监控业务进程管家：拉起 / 停 / 重启 order-api，读金丝雀和题号。

【在 Agent 里的位置】
    FastAPI 启动时 start_target；Agent 工具 restart_service；watchdog 每 8s snapshot。
    热修 write_scope 靠 is_target_workspace 判断当前工作区是不是这个仓。

【调用关系】
    main.lifespan、tools.restart_service / run_tests、watchdog.tick、duty 落地页、评测脚本。
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

from app.core.config import REPO_ROOT, get_settings

log = logging.getLogger("forge.target")


def target_root() -> Path:
    """
    【业务】被盯着的小商店代码在哪个文件夹。默认是仓库里的 targets/order-api。
    【什么时候用】读题号、拉起进程、判断热修工作区、跑宿主 pytest，都先问这里。
    【谁调用】本文件几乎所有函数；watchdog / duty / make_forge_context 间接用到。
    【参数】无。可用 .env 的 FORGE_TARGET_CWD 覆盖。
    【里面调用】get_settings()。
    【是否主线】是。热修工具的根目录就是它。
    """
    s = get_settings()
    raw = (s.forge_target_cwd or "").strip()
    path = Path(raw) if raw else REPO_ROOT / "targets" / "order-api"
    return path.resolve()


def target_name() -> str:
    """
    【业务】告警标题、pid 文件名用的服务名，默认 order-api。
    【什么时候用】组告警、写 data/targets/{name}.pid。
    【谁调用】watchdog、snapshot、pid_path / log_path。
    【参数】无。FORGE_TARGET_NAME 可改。
    【是否主线】帮手。
    """
    return (get_settings().forge_target_name or "order-api").strip() or "order-api"


def health_url() -> str:
    """
    【业务】进程还活着吗？打这个地址。活着不等于业务对，对不对看 canary。
    【什么时候用】snapshot 拼健康字段。
    【谁调用】snapshot。
    【参数】无。默认 http://127.0.0.1:8790/health。
    【是否主线】帮手。
    """
    return (get_settings().forge_target_health or "http://127.0.0.1:8790/health").strip()


def canary_url() -> str:
    """
    【业务】下单对不对？打 /canary。500 了 watchdog 才往收件箱塞纸条。
    【什么时候用】snapshot、watchdog.tick。
    【谁调用】snapshot。
    【参数】无。默认 http://127.0.0.1:8790/canary。
    【是否主线】是。热修第一环的探针地址。
    """
    return (get_settings().forge_target_canary or "http://127.0.0.1:8790/canary").strip()


def pid_path() -> Path:
    """
    【业务】Forge 自己拉起的 order-api，pid 记在这个文件里，停/重启才不会杀错进程。
    【什么时候用】start / stop / current_pid。
    【谁调用】current_pid、start_target、stop_target。
    【参数】无。
    【是否主线】帮手。
    """
    folder = REPO_ROOT / "data" / "targets"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{target_name()}.pid"


def log_path() -> Path:
    """
    【业务】order-api 的打印落到这个日志文件。排查稿会截一段尾巴给模型看。
    【什么时候用】拉起进程时重定向 stdout；snapshot / 排查 prompt 读尾。
    【谁调用】start_target、tail_log、snapshot。
    【参数】无。
    【是否主线】帮手。
    """
    folder = REPO_ROOT / "data" / "targets"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{target_name()}.log"


def active_bug() -> int:
    """
    【业务】当前激活的是第几题（1～10）。bugs.py 里十个缺陷互斥，只放这一题。
    【什么时候用】watchdog 去重、排查稿写「当前 BUG-n」、落地页展示。
    【谁调用】snapshot、watchdog.tick。
    【参数】无。读 target_root()/active_bug.txt；读坏了当 1。
    【是否主线】是。
    """
    f = target_root() / "active_bug.txt"
    try:
        return int(f.read_text(encoding="utf-8").strip() or "1")
    except (OSError, ValueError):
        return 1


def set_active_bug(n: int) -> int:
    """
    【业务】落地页点「下一题」：把题号写进 active_bug.txt。不重启进程的话，
            正在跑的代码可能还是旧题，通常还要再 restart。
    【什么时候用】duty 落地页切题。
    【谁调用】duty.next_bug 一类接口。
    【参数】n：想切到的题号，会被夹到 1～10。返回实际写下的数字。
    【是否主线】落地页。雷达去重认标题里的 BUG-n。
    """
    n = max(1, min(int(n), 10))
    (target_root() / "active_bug.txt").write_text(str(n) + "\n", encoding="utf-8")
    return n


def is_target_workspace(workspace: Path) -> bool:
    """
    【业务】这次对话的工作区是不是被监控的 order-api。是：热修只能改 bugs.py/app.py，
            并允许 restart_service。不是：当普通仓库，不要锁死别人的项目。
    【什么时候用】make_forge_context 决定 write_scope=hotfix 还是 workspace。
    【谁调用】agent_runtime.make_forge_context。
    【参数】workspace：当前会话的根目录 Path。
    【里面调用】target_root()，比两个 resolve 后的路径。
    【是否主线】是。防模型去改 tests/ 或题号文件。
    """
    try:
        return workspace.resolve() == target_root()
    except OSError:
        return False


def _pid_alive(pid: int) -> bool:
    """
    【业务】这个进程号对应的程序还在不在。Windows 用 tasklist，其它用 kill(0) 探测。
    【什么时候用】读完 pid 文件后确认没过期。
    【谁调用】current_pid。
    【参数】pid：正整数进程号。
    【是否主线】帮手。
    """
    if pid <= 0:
        return False
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        return str(pid) in (result.stdout or "")
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def current_pid() -> int | None:
    """
    【业务】Forge 拉起的 order-api 现在的 pid。文件没有或进程已死则 None。
    【什么时候用】要不要再 start、snapshot 里 running 字段。
    【谁调用】start_target、stop_target、snapshot。
    【参数】无。
    【里面调用】pid_path、_pid_alive。
    【是否主线】帮手。
    """
    path = pid_path()
    if not path.is_file():
        return None
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except ValueError:
        return None
    return pid if _pid_alive(pid) else None


def start_command() -> list[str]:
    """
    【业务】怎么把小商店跑起来。默认：当前 Python -m uvicorn app:app --port 8790。
    【什么时候用】start_target 里 Popen。
    【谁调用】start_target。
    【参数】无。FORGE_TARGET_CMD 可整句覆盖。
    【是否主线】帮手。
    """
    s = get_settings()
    raw = (s.forge_target_cmd or "").strip()
    if raw:
        return raw.split()
    python = sys.executable
    return [python, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", "8790"]


def start_target() -> dict:
    """
    【业务】order-api 没在跑就拉起来，已经在跑就不动。Forge 启动 lifespan 会调一次，
            这样金丝雀才有人可打。
    【什么时候用】API 启动；落地页点启动；restart 的后半段。
    【谁调用】main.lifespan、duty.start、restart_target。
    【参数】无。返回 {ok, status: already_running|started, pid}。
    【里面调用】current_pid、target_root、start_command、写 pid 文件。
    【是否主线】是。没有进程，watchdog 只能报 down。
    """
    alive = current_pid()
    if alive:
        return {"ok": True, "status": "already_running", "pid": alive}
    cwd = target_root()
    if not cwd.is_dir():
        raise RuntimeError(f"业务目录不存在: {cwd}")
    logf = log_path().open("ab")
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
            subprocess, "CREATE_NO_WINDOW", 0
        )
    proc = subprocess.Popen(
        start_command(),
        cwd=str(cwd),
        stdout=logf,
        stderr=subprocess.STDOUT,
        creationflags=flags,
    )
    pid_path().write_text(str(proc.pid), encoding="utf-8")
    return {"ok": True, "status": "started", "pid": proc.pid}


def stop_target() -> dict:
    """
    【业务】停掉 Forge 拉起的那份 order-api，并删 pid 文件。
    【什么时候用】落地页停止；restart 的前半段。Agent 热修复好后一般走 restart 不是只停。
    【谁调用】duty.stop、restart_target。
    【参数】无。
    【里面调用】current_pid；Windows taskkill / 其它 SIGTERM。
    【是否主线】落地页。
    """
    pid = current_pid()
    if not pid:
        return {"ok": True, "status": "already_stopped"}
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=20)
    else:
        os.kill(pid, 15)
        time.sleep(0.4)
    try:
        pid_path().unlink(missing_ok=True)
    except OSError:
        pass
    return {"ok": True, "status": "stopped", "pid": pid}


def restart_target() -> dict:
    """
    【业务】先停再起，让 :8790 加载磁盘上改过的 bugs.py。测试通过后模型才会调到这一步。
    【什么时候用】工具 restart_service；落地页重启。
    【谁调用】tools.restart_service、duty.restart。
    【参数】无。
    【里面调用】stop_target、sleep、start_target。
    【是否主线】是。金丝雀变绿要等进程吃到新代码。
    """
    stopped = stop_target()
    time.sleep(0.6)
    started = start_target()
    return {"ok": True, "stopped": stopped, "started": started}


def probe(url: str, timeout: float = 3.0) -> dict:
    """
    【业务】对一个 URL 发 GET。200～299 算 ok。连不上 status_code=0，body 是错误字。
    【什么时候用】打 health 和 canary。
    【谁调用】snapshot。
    【参数】url：完整地址；timeout：秒，默认 3。
    【里面调用】httpx.get。
    【是否主线】是（经 snapshot 给 watchdog）。
    """
    try:
        res = httpx.get(url, timeout=timeout, trust_env=False)
        body = res.text[:2000]
        return {"ok": 200 <= res.status_code < 300, "status_code": res.status_code, "body": body, "url": url}
    except Exception as exc:
        return {"ok": False, "status_code": 0, "body": str(exc), "url": url}


def tail_log(max_lines: int = 160) -> str:
    """
    【业务】取出 order-api 日志最后若干行，塞进排查稿，模型才能对照堆栈。
    【什么时候用】snapshot、format_investigation_prompt。
    【谁调用】snapshot。
    【参数】max_lines：想要的行数，实际夹在 20～400。
    【是否主线】帮手，排查稿要用。
    """
    path = log_path()
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines = text.splitlines()
    return "\n".join(lines[-max(20, min(max_lines, 400)) :])


def bug_catalog() -> list[dict]:
    """
    【业务】读 bugs.py 里的 catalog()，落地页列出十题标题和提示。
    【什么时候用】snapshot 带给落地页。
    【谁调用】snapshot。
    【参数】无。
    【里面调用】动态 import target_root()/bugs.py。
    【是否主线】落地页。
    """
    path = target_root() / "bugs.py"
    if not path.is_file():
        return []
    import importlib.util

    spec = importlib.util.spec_from_file_location("forge_target_bugs", path)
    if not spec or not spec.loader:
        return []
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    items = getattr(mod, "catalog", lambda: [])()
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "id": int(item.get("id") or 0),
                "title": str(item.get("title") or ""),
                "hint": str(item.get("hint") or ""),
            }
        )
    return out


def snapshot() -> dict:
    """
    【业务】此刻小商店怎么样：进程在不在、第几题、health/canary 红不红、日志尾巴。
            watchdog 每 8 秒拍一次；排查稿也用同一份，避免重复打 canary。
    【什么时候用】雷达 tick；组排查稿；落地页刷新状态。
    【谁调用】watchdog.tick、duty.format_investigation_prompt、duty.target_status。
    【参数】无。返回一个大字典（name/cwd/pid/running/active_bug/health/canary/...）。
    【里面调用】current_pid、probe、active_bug、bug_catalog、tail_log。
    【是否主线】是。第一环和第二环共用这一眼。
    """
    pid = current_pid()
    health = probe(health_url())
    canary = probe(canary_url())
    n = active_bug()
    return {
        "name": target_name(),
        "cwd": str(target_root()),
        "pid": pid,
        "running": bool(pid),
        "active_bug": n,
        "health": health,
        "canary": canary,
        "health_url": health_url(),
        "canary_url": canary_url(),
        "fixed": bool(pid) and bool(canary.get("ok")),
        "catalog": bug_catalog(),
        "log_tail": tail_log(),
        "log_path": str(log_path()),
    }


def run_host_tests() -> str:
    """
    【业务】在值班机本机跑 pytest tests（order-api 带 .forge-host-tests，不走 Docker）。
            测的是磁盘上的代码，不会替换正在 :8790 跑的进程。
    【什么时候用】模型调 run_tests 且判定走宿主。
    【谁调用】tools.run_tests。
    【参数】无。返回 pytest 输出文本，失败带 [exit 码]。
    【里面调用】subprocess pytest -q tests，超时 120 秒。
    【是否主线】是。先测通过再重启。
    """
    cwd = target_root()
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "tests"],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=120,
        encoding="utf-8",
        errors="replace",
    )
    out = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
    if result.returncode != 0:
        return f"[exit {result.returncode}]\n{out}"
    return out or "(tests passed)"
