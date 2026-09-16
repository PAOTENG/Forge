"""Forge 分层评测执行器。

本脚本只跑当前可复现、不调用大模型、不绕过 HITL 的测试：
1. 后端确定性回归
2. 十题故障验收器（FAIL_TO_PASS / PASS_TO_PASS 风格）
3. 运行时探针（API / 金丝雀 / 沙箱）
4. 前端生产构建

原始结果写入 项目测试/原始结果/
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(__file__).resolve().parent / "原始结果"
BACKEND = ROOT / "backend"
TARGET = ROOT / "targets" / "order-api"
ACTIVE = TARGET / "active_bug.txt"
FRONTEND = ROOT / "frontend"
PY = Path(sys.executable)


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def run(cmd: list[str], cwd: Path, timeout: int = 180) -> dict:
    t0 = time.time()
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
    )
    return {
        "cmd": cmd,
        "cwd": str(cwd),
        "returncode": proc.returncode,
        "seconds": round(time.time() - t0, 3),
        "stdout": proc.stdout[-20000:],
        "stderr": proc.stderr[-12000:],
    }


def http_get(url: str, timeout: float = 3.0) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as res:
            body = res.read().decode("utf-8", errors="replace")
            return {
                "ok": 200 <= res.status < 300,
                "status": res.status,
                "body": body[:2000],
            }
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        return {"ok": False, "status": exc.code, "body": body[:2000]}
    except Exception as exc:
        return {"ok": False, "status": 0, "body": str(exc)}


def backend_pytest() -> dict:
    return run(
        [str(PY), "-m", "pytest", "-q", "--tb=line"],
        BACKEND,
        timeout=120,
    )


def frontend_build() -> dict:
    pnpm = "pnpm.cmd" if os.name == "nt" else "pnpm"
    return run([pnpm, "build"], FRONTEND, timeout=180)


def docker_sandbox() -> dict:
    listing = run(
        [
            "docker",
            "ps",
            "--filter",
            "name=forge-",
            "--format",
            "{{.Names}}\t{{.Image}}\t{{.Status}}",
        ],
        ROOT,
        timeout=30,
    )
    inspect = run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            (
                "docker ps --filter name=forge- --format '{{.Names}}' | "
                "ForEach-Object { docker inspect --format "
                "'{{.Name}} network={{.HostConfig.NetworkMode}} "
                "memory={{.HostConfig.Memory}} pids={{.HostConfig.PidsLimit}}' $_ }"
            ),
        ],
        ROOT,
        timeout=40,
    )
    return {"ps": listing, "inspect": inspect}


def live_probes() -> dict:
    return {
        "forge_health": http_get("http://127.0.0.1:8787/api/health"),
        "target_health": http_get("http://127.0.0.1:8790/health"),
        "target_canary": http_get("http://127.0.0.1:8790/canary"),
    }


def bug_oracle() -> dict:
    """SWE-bench 风格：每个故障应让验收失败；健康样例应通过。

    使用 FastAPI TestClient，不依赖 :8790 进程。会短暂改写 active_bug.txt，结束后恢复。
    """
    original = ACTIVE.read_text(encoding="utf-8") if ACTIVE.is_file() else "1\n"
    rows: list[dict] = []
    try:
        sys.path.insert(0, str(TARGET))
        from fastapi.testclient import TestClient

        import app as order_app
        from bugs import STOCK, catalog, create_order

        client = TestClient(order_app.app)
        items = catalog()
        for item in items:
            n = int(item["id"])
            ACTIVE.write_text(f"{n}\n", encoding="utf-8")
            STOCK["sku-1"] = 8
            canary = client.get("/canary")
            STOCK["sku-1"] = 8
            order = client.post(
                "/orders",
                json={"user_id": "u1", "sku": "sku-1", "qty": 1, "price": 20, "coupon": None},
            )
            STOCK["sku-1"] = 8
            healthy = None
            try:
                data = create_order(
                    {"user_id": "u1", "sku": "sku-1", "qty": 1, "price": 20, "coupon": None}
                )
                healthy = {
                    "raised": False,
                    "has_order_id": "order_id" in data,
                    "status": data.get("status"),
                    "total": data.get("total"),
                    "stock": STOCK.get("sku-1"),
                }
            except Exception as exc:
                healthy = {"raised": True, "error": f"{type(exc).__name__}: {exc}"}

            pytest_like_pass = (
                not healthy["raised"]
                and healthy.get("has_order_id") is True
                and healthy.get("status") == "paid"
                and abs(float(healthy.get("total") or 0) - 30) < 0.01
                and healthy.get("stock") == 7
            )
            rows.append(
                {
                    "id": n,
                    "title": item.get("title"),
                    "hint": item.get("hint"),
                    "canary_status": canary.status_code,
                    "canary_fail": canary.status_code >= 400,
                    "canary_body": (canary.text or "")[:400],
                    "standard_order_status": order.status_code,
                    "pytest_equivalent_pass": pytest_like_pass,
                    "healthy_sample": healthy,
                    "oracle_quality": (
                        "ok_fail_to_pass"
                        if (canary.status_code >= 400 and not pytest_like_pass)
                        else "weak_or_broken_oracle"
                    ),
                }
            )
    finally:
        ACTIVE.write_text(original, encoding="utf-8")
        if str(TARGET) in sys.path:
            sys.path.remove(str(TARGET))
    fail_to_pass_ok = sum(1 for r in rows if r["oracle_quality"] == "ok_fail_to_pass")
    return {
        "original_active_bug": original.strip(),
        "restored": ACTIVE.read_text(encoding="utf-8").strip(),
        "bugs": rows,
        "fail_to_pass_working": fail_to_pass_ok,
        "fail_to_pass_total": len(rows),
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    report = {
        "started_at": now(),
        "python": str(PY),
        "layers": {},
    }
    print("== layer1 backend pytest ==")
    report["layers"]["backend_pytest"] = backend_pytest()
    print("returncode", report["layers"]["backend_pytest"]["returncode"])

    print("== layer2 bug oracle ==")
    report["layers"]["bug_oracle"] = bug_oracle()
    print(
        "fail_to_pass",
        report["layers"]["bug_oracle"]["fail_to_pass_working"],
        "/",
        report["layers"]["bug_oracle"]["fail_to_pass_total"],
    )

    print("== layer3 live probes ==")
    report["layers"]["live_probes"] = live_probes()
    report["layers"]["sandbox"] = docker_sandbox()

    print("== layer4 frontend build ==")
    report["layers"]["frontend_build"] = frontend_build()
    print("returncode", report["layers"]["frontend_build"]["returncode"])

    report["finished_at"] = now()
    path = OUT / "本次评测原始结果.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", path)


if __name__ == "__main__":
    main()
