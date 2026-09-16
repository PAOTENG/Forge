"""
无头任务评测（简历 pass@1=6/7 的出处）。

【在 Agent 里的位置】
    真跑 OpenAI Agents SDK：auto_approve=True，allow_git_push=False。
    每题 git reset、只激活一题、score_trial 用 pytest + 活进程 /canary。
    BUG-2/3/4 验收器失效不进分母。

【关键函数】
    restore_workspace / run_one_trial / score_trial / async_main
"""

Runs the real Coding Agent against targets/order-api with:
- ForgeContext.auto_approve=True (admin/eval, no browser HITL)
- allow_git_push=False (git_push is a no-op)
- git reset after every trial
- pytest + live /canary scoring

Invalid-oracle tasks (BUG-2/3/4) are recorded but never credited.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
TARGET = ROOT / "targets" / "order-api"
OUT_ROOT = Path(__file__).resolve().parent / "原始结果"
PY = Path(sys.executable)

sys.path.insert(0, str(BACKEND))

INVALID_ORACLE = {2, 3, 4}
WEAK_ORACLE = {6}
DEFAULT_BUGS = [1, 5, 6, 7, 8, 9, 10]
MUTATING = {
    "write_file",
    "delete_file",
    "bash",
    "git_checkout",
    "git_commit",
    "git_push",
    "run_tests",
    "restart_service",
}
PRESERVE_UNTRACKED = {".gitignore", "AGENTS.md", "FORGE.md"}

log = logging.getLogger("forge.eval")


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def git(args: list[str], cwd: Path = TARGET, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
        timeout=60,
    )


def git_head() -> str:
    return (git(["rev-parse", "HEAD"]).stdout or "").strip()


def git_untracked() -> set[str]:
    out = git(["ls-files", "--others", "--exclude-standard"]).stdout or ""
    return {line.strip().replace("\\", "/") for line in out.splitlines() if line.strip()}


def restore_workspace(head: str, keep_untracked: set[str]) -> dict:
    """【功能】每题开始前 git reset 到固定 HEAD，避免上一题污染。"""
    reset = git(["reset", "--hard", head])
    extra = git_untracked() - keep_untracked - PRESERVE_UNTRACKED
    removed: list[str] = []
    for rel in sorted(extra):
        path = TARGET / rel
        try:
            if path.is_file():
                path.unlink()
                removed.append(rel)
        except OSError:
            pass
    return {
        "head": head,
        "reset_code": reset.returncode,
        "reset_stderr": (reset.stderr or "")[:800],
        "removed_untracked": removed,
        "status": (git(["status", "--short"]).stdout or "")[:2000],
    }


def run_pytest() -> dict:
    """【功能】宿主跑 order-api/tests，给 score_trial 的 pytest 分。"""
    t0 = time.time()
    proc = subprocess.run(
        [str(PY), "-m", "pytest", "-q", "--tb=line", "tests"],
        cwd=str(TARGET),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    return {
        "returncode": proc.returncode,
        "ok": proc.returncode == 0,
        "seconds": round(time.time() - t0, 3),
        "stdout": (proc.stdout or "")[-8000:],
        "stderr": (proc.stderr or "")[-4000:],
    }


def wait_http(url: str, timeout: float = 20.0) -> dict:
    import httpx

    deadline = time.time() + timeout
    last: dict = {"ok": False, "status_code": 0, "body": "timeout", "url": url}
    while time.time() < deadline:
        try:
            res = httpx.get(url, timeout=2.5, trust_env=False)
            last = {
                "ok": 200 <= res.status_code < 300,
                "status_code": res.status_code,
                "body": (res.text or "")[:2000],
                "url": url,
            }
            if last["ok"] or last["status_code"] >= 400:
                return last
        except Exception as exc:
            last = {"ok": False, "status_code": 0, "body": str(exc), "url": url}
        time.sleep(0.4)
    return last


def eval_prompt(bug: int, title: str, canary: dict) -> str:
    from app.services.duty import format_investigation_prompt
    from app.services.supervisor import snapshot, target_root

    state = snapshot()
    body = format_investigation_prompt(
        {
            "title": f"order-api 金丝雀失败 · BUG-{bug}",
            "summary": f"评测任务：只修当前激活的 BUG-{bug}（{title}）。",
            "service": "order-api",
            "severity": "critical",
            "link": state.get("canary_url") or "http://127.0.0.1:8790/canary",
            "stack": (canary.get("body") or "")[:4000],
        },
        state=state,
    )
    extra = [
        "",
        "【评测模式，必须遵守】",
        f"工作区就是业务仓库：{target_root()}",
        "本会话是管理员评测会话：变更工具已自动批准，不必等待人工点允许。",
        "禁止 git_push。即使调用也会被跳过，不要反复尝试。",
        "禁止修改 tests/、禁止修改或删除 active_bug.txt、禁止改其它 BUG 分支。",
        "禁止 force push、git reset --hard、大重构。",
        "顺序：只读定位 → write_file 最小热修 → run_tests → 可选 git_commit → 最后 restart_service。",
        "金丝雀变绿才算这题完成。测过再重启，不要靠重启去试错。",
    ]
    return body + "\n".join(extra)


def summarize_events(events: list[dict]) -> dict:
    tools: list[dict] = []
    files: list[str] = []
    texts: list[str] = []
    errors: list[str] = []
    approvals = 0
    for ev in events:
        kind = ev.get("type")
        if kind == "tool_called":
            name = str(ev.get("tool") or "")
            args = str(ev.get("arguments") or "")
            rec = {"tool": name, "arguments": args[:4000]}
            tools.append(rec)
            if name in {"write_file", "delete_file"}:
                try:
                    path = json.loads(args or "{}").get("path")
                    if path:
                        files.append(str(path).replace("\\", "/"))
                except json.JSONDecodeError:
                    pass
        elif kind == "approval_needed":
            approvals += 1
        elif kind == "error":
            errors.append(str(ev.get("message") or "")[:1000])
        elif kind == "done":
            texts.append(str(ev.get("text") or "")[:4000])
        elif kind == "message_output_created":
            texts.append(str(ev.get("text") or "")[:4000])
    return {
        "tool_calls": len(tools),
        "tools": tools,
        "tool_names": [t["tool"] for t in tools],
        "files_touched": sorted(set(files)),
        "auto_resume_approvals": approvals,
        "errors": errors,
        "final_text": texts[-1] if texts else "",
        "called_git_push": "git_push" in [t["tool"] for t in tools],
        "called_run_tests": "run_tests" in [t["tool"] for t in tools],
        "called_restart": "restart_service" in [t["tool"] for t in tools],
        "called_write": "write_file" in [t["tool"] for t in tools],
    }


def score_trial(
    bug: int,
    baseline_canary: dict,
    pytest_after: dict,
    canary_after: dict,
    canary_forced: dict,
    summary: dict,
    tests_diff: str,
    active_after: str,
) -> dict:
    """
    【功能】一道题的打分：pytest 绿 + 活金丝雀 200 才算过；改测试/改题号/偷 push 记作弊。
    【输入】题号、基线金丝雀、测后结果、工具摘要、tests diff、结束后的 active_bug。
    【输出】含 credit / product_resolved / cheat 标志的字典。INVALID_ORACLE 不进分母。
    """
    baseline_fail = int(baseline_canary.get("status_code") or 0) >= 400
    pytest_ok = bool(pytest_after.get("ok"))
    canary_ok = bool(canary_after.get("ok"))
    patch_ok = pytest_ok and bool(canary_forced.get("ok"))
    tests_tampered = bool(tests_diff.strip())
    bug_tampered = (active_after or "").strip() != str(bug)
    credit_allowed = bug not in INVALID_ORACLE and baseline_fail
    product_resolved = bool(pytest_ok and canary_ok and not tests_tampered and not bug_tampered)
    if bug in WEAK_ORACLE:
        product_resolved = bool(canary_ok and not tests_tampered and not bug_tampered)
    credited = bool(credit_allowed and product_resolved)
    return {
        "oracle": (
            "invalid_do_not_credit"
            if bug in INVALID_ORACLE
            else "weak_canary_only"
            if bug in WEAK_ORACLE
            else "ok_fail_to_pass"
        ),
        "baseline_canary_failed": baseline_fail,
        "credit_allowed": credit_allowed,
        "pytest_ok": pytest_ok,
        "canary_ok": canary_ok,
        "patch_correct_after_forced_restart": patch_ok,
        "tests_tampered": tests_tampered,
        "active_bug_tampered": bug_tampered,
        "product_resolved": product_resolved,
        "credited_success": credited,
        "reason": (
            "invalid oracle: baseline canary already green"
            if bug in INVALID_ORACLE
            else "baseline canary was not red; not a legal FAIL_TO_PASS"
            if not baseline_fail
            else "tests/ modified"
            if tests_tampered
            else "active_bug.txt changed"
            if bug_tampered
            else "product resolved"
            if credited
            else "agent did not turn pytest+canary green"
        ),
        **{k: summary[k] for k in (
            "tool_calls",
            "tool_names",
            "files_touched",
            "auto_resume_approvals",
            "called_git_push",
            "called_run_tests",
            "called_restart",
            "called_write",
        )},
    }


async def audit(session_id: str, tool: str, arguments: str, decision: str) -> None:
    from app.db.session import get_session_factory
    from app.models.audit import AuditRow

    factory = get_session_factory()
    async with factory() as db:
        db.add(
            AuditRow(
                session_id=session_id,
                tool_name=(tool or "eval")[:80],
                arguments=(arguments or "")[:8000],
                decision=decision[:32],
                actor="eval-admin",
            )
        )
        await db.commit()


async def create_eval_session(workspace: Path, title: str, model: str) -> str:
    from app.db.session import get_session_factory
    from app.models.session import SessionRow
    from app.services.messages import add_message

    factory = get_session_factory()
    async with factory() as db:
        row = SessionRow(
            title=title[:200],
            workspace_path=str(workspace),
            model=model,
            mode="build",
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        await add_message(db, row.id, "system", "eval auto-approve session", kind="eval")
        return row.id


async def drain_run(
    session_id: str,
    workspace: Path,
    model: str,
    timeout: float,
) -> tuple[list[dict], str]:
    """
    【功能】评测里消费一次 Agent 流：遇到 approval_needed 就自动 start_resume(approved=True)。
    【输入】会话、工作区、模型、超时秒。输出：(事件列表, 终止原因)。
    """
    from app.services.agent_runtime import cancel_run, start_resume, subscribe_events

    events: list[dict] = []
    after = 0
    deadline = time.time() + timeout
    terminal = "timeout"
    auto_resumes = 0
    while time.time() < deadline:
        remaining = max(1.0, deadline - time.time())

        async def collect() -> str | None:
            nonlocal after
            async for ev in subscribe_events(session_id, after=after):
                seq = int(ev.get("seq") or after)
                after = max(after, seq)
                if ev.get("type") == "ping":
                    continue
                slim = {k: v for k, v in ev.items() if k != "text" or True}
                if "text" in slim and isinstance(slim["text"], str) and len(slim["text"]) > 6000:
                    slim["text"] = slim["text"][:6000]
                if "output" in slim and isinstance(slim["output"], str) and len(slim["output"]) > 6000:
                    slim["output"] = slim["output"][:6000]
                if "arguments" in slim and isinstance(slim["arguments"], str) and len(slim["arguments"]) > 6000:
                    slim["arguments"] = slim["arguments"][:6000]
                slim["ts"] = now()
                events.append(slim)
                kind = slim.get("type")
                if kind in {"done", "error", "cancelled", "approval_needed"}:
                    return str(kind)
            return None

        try:
            kind = await asyncio.wait_for(collect(), timeout=remaining)
        except TimeoutError:
            cancel_run(session_id)
            return events, "timeout"
        if kind == "approval_needed":
            auto_resumes += 1
            pending = events[-1] if events else {}
            await audit(
                session_id,
                str(pending.get("tool") or "approval"),
                str(pending.get("arguments") or ""),
                "eval-auto-allow",
            )
            if auto_resumes > 24:
                cancel_run(session_id)
                return events, "too_many_approvals"
            await start_resume(
                session_id,
                workspace,
                "build",
                True,
                True,
                model,
                actor="eval-admin",
                role="admin",
                auto_approve=True,
                allow_git_push=False,
            )
            continue
        return events, kind or "unknown"
    cancel_run(session_id)
    return events, "timeout"


async def run_one_trial(
    bug: int,
    title: str,
    model: str,
    timeout: int,
    head: str,
    keep_untracked: set[str],
    out_dir: Path,
) -> dict:
    """
    【功能】评一道题：重置仓、只激活该题、start_prompt、drain、打分、写 trial JSON。
    【输入】bug 号、标题、模型、超时、git HEAD、保留的未跟踪文件、输出目录。
    【输出】该 trial 的结果字典。
    """
    from app.services.agent_runtime import model_label, start_prompt
    from app.services.sandbox import SandboxError, sandbox_manager
    from app.services.supervisor import (
        canary_url,
        health_url,
        restart_target,
        set_active_bug,
        snapshot,
        target_root,
    )

    workspace = target_root()
    restore_workspace(head, keep_untracked)
    set_active_bug(bug)
    restart_target()
    health = wait_http(health_url(), timeout=25)
    baseline_canary = wait_http(canary_url(), timeout=8)
    baseline_pytest = run_pytest()
    started = now()
    t0 = time.time()
    session_id = await create_eval_session(workspace, f"eval BUG-{bug} {title}", model or model_label())
    sandbox_note = ""
    try:
        sandbox_manager.ensure(session_id, workspace)
    except SandboxError as exc:
        sandbox_note = str(exc)

    prompt = eval_prompt(bug, title, baseline_canary)
    await start_prompt(
        session_id,
        workspace,
        "build",
        prompt,
        model or None,
        actor="eval-admin",
        role="admin",
        auto_approve=True,
        allow_git_push=False,
    )
    events, terminal = await drain_run(session_id, workspace, model, timeout)
    elapsed = round(time.time() - t0, 3)
    summary = summarize_events(events)
    for rec in summary["tools"]:
        if rec["tool"] in MUTATING:
            await audit(session_id, rec["tool"], rec["arguments"], "eval-auto-exec")

    pytest_after = run_pytest()
    canary_after = wait_http(canary_url(), timeout=8)
    restart_target()
    wait_http(health_url(), timeout=25)
    canary_forced = wait_http(canary_url(), timeout=8)
    tests_diff = git(["diff", head, "--", "tests"]).stdout or ""
    active_after = (TARGET / "active_bug.txt").read_text(encoding="utf-8", errors="replace")
    git_diff = git(["diff", head, "--", "bugs.py", "app.py"]).stdout or ""
    scoring = score_trial(
        bug,
        baseline_canary,
        pytest_after,
        canary_after,
        canary_forced,
        summary,
        tests_diff,
        active_after,
    )
    snap = snapshot()
    try:
        sandbox_manager.stop(session_id)
    except Exception:
        pass
    restore = restore_workspace(head, keep_untracked)
    row = {
        "bug": bug,
        "title": title,
        "session_id": session_id,
        "model": model,
        "started_at": started,
        "finished_at": now(),
        "elapsed_seconds": elapsed,
        "terminal": terminal,
        "sandbox_note": sandbox_note,
        "baseline": {
            "health": health,
            "canary": baseline_canary,
            "pytest": baseline_pytest,
            "snapshot_active_bug": snap.get("active_bug"),
        },
        "after_agent": {
            "pytest": pytest_after,
            "canary": canary_after,
            "canary_after_forced_restart": canary_forced,
            "tests_diff": tests_diff[:8000],
            "code_diff": git_diff[:12000],
            "active_bug.txt": active_after.strip(),
        },
        "trajectory_summary": summary,
        "final_text": summary.get("final_text") or "",
        "scoring": scoring,
        "restore": restore,
        "event_count": len(events),
    }
    bug_dir = out_dir / f"BUG-{bug}"
    bug_dir.mkdir(parents=True, exist_ok=True)
    (bug_dir / "trial-1.json").write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
    with (bug_dir / "trial-1.traj.jsonl").open("w", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
    return row


def catalog_map() -> dict[int, str]:
    from app.services.supervisor import bug_catalog

    return {int(item["id"]): str(item.get("title") or "") for item in bug_catalog()}


async def async_main(bugs: list[int], timeout: int) -> dict:
    from app.services.agent_runtime import model_label
    from app.services.supervisor import restart_target, set_active_bug
    from app.services.supervisor import health_url

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = OUT_ROOT / f"agent_eval_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    head = git_head()
    keep_untracked = git_untracked()
    model = model_label()
    titles = catalog_map()
    trials: list[dict] = []
    report = {
        "started_at": now(),
        "python": str(PY),
        "model": model,
        "timeout_seconds": timeout,
        "trials_per_task": 1,
        "workspace": str(TARGET),
        "git_head": head,
        "auto_approve": True,
        "allow_git_push": False,
        "bugs": bugs,
        "trials": trials,
    }
    try:
        for bug in bugs:
            title = titles.get(bug, f"BUG-{bug}")
            log.info("==== eval BUG-%s %s ====", bug, title)
            try:
                row = await run_one_trial(bug, title, model, timeout, head, keep_untracked, out_dir)
            except Exception:
                row = {
                    "bug": bug,
                    "title": title,
                    "terminal": "harness_error",
                    "error": traceback.format_exc()[-4000:],
                    "scoring": {
                        "credited_success": False,
                        "credit_allowed": bug not in INVALID_ORACLE,
                        "reason": "harness_error",
                    },
                }
                restore_workspace(head, keep_untracked)
            trials.append(row)
            scoring = row.get("scoring") or {}
            log.info(
                "BUG-%s terminal=%s credited=%s reason=%s tools=%s",
                bug,
                row.get("terminal"),
                scoring.get("credited_success"),
                scoring.get("reason"),
                scoring.get("tool_calls"),
            )
    finally:
        restore_workspace(head, keep_untracked)
        set_active_bug(5)
        restart_target()
        wait_http(health_url(), timeout=25)
        report["restored_active_bug"] = (TARGET / "active_bug.txt").read_text(encoding="utf-8").strip()
        report["restored_git"] = (git(["status", "--short"]).stdout or "").strip()
        report["finished_at"] = now()

    credited = [t for t in trials if (t.get("scoring") or {}).get("credited_success")]
    eligible = [t for t in trials if (t.get("scoring") or {}).get("credit_allowed")]
    report["summary"] = {
        "tasks_run": len(trials),
        "eligible_fail_to_pass": len(eligible),
        "credited_resolved": len(credited),
        "pass_at_1": (len(credited) / len(eligible)) if eligible else None,
        "resolved_bugs": [t.get("bug") for t in credited],
        "failed_eligible": [
            t.get("bug")
            for t in eligible
            if not (t.get("scoring") or {}).get("credited_success")
        ],
    }
    summary_path = out_dir / "summary.json"
    summary_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    latest = OUT_ROOT / "L5-Agent任务评测原始结果.json"
    latest.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("wrote %s", summary_path)
    return report


def parse_bugs(raw: str) -> list[int]:
    return [int(x.strip()) for x in raw.split(",") if x.strip()]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bugs", default=",".join(str(n) for n in DEFAULT_BUGS))
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()
    report = asyncio.run(async_main(parse_bugs(args.bugs), args.timeout))
    summary = report.get("summary") or {}
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUTF8", "1")
    main()
