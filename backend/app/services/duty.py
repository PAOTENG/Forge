"""
值班通道：告警/批准推企微钉钉，以及排查 prompt、pending token。

【在 Agent 里的位置】
    计算仍在值班电脑。这里只负责「把人点进来」：textcard / actionCard / 通用 webhook。
    批准卡链到 /api/duty/card；告警链到 Vite 值班台。

【调用关系】
    watchdog / ingest → notify_alert；chat approval_needed → notify_approval；
    duty.investigate 用 format_investigation_prompt 自动发第一条用户消息。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import time
import urllib.parse
from uuid import uuid4

import httpx

from app.core.config import get_settings

log = logging.getLogger("forge.duty")

_wecom_token = {"value": "", "exp": 0.0}
pending_tokens: dict[str, str] = {}


def channel_status() -> dict:
    """
    【业务】落地页问三通道和值班工作区配好了没有，不发消息。
    【技术】只读 Settings。wecom/dingtalk/webhook 是「密钥是否齐全」的布尔。
    【谁调用】duty_status、test_notify、_fanout 决定往哪发。
    """
    from app.services.supervisor import target_root

    s = get_settings()

    workspace = s.forge_duty_workspace.strip() or str(target_root())
    return {
        "public_url": s.forge_public_url.rstrip("/"),
        "workspace": workspace,
        "wecom": bool(s.wecom_corp_id and s.wecom_corp_secret and s.wecom_agent_id),
        "dingtalk": bool(s.dingtalk_webhook),
        "webhook": bool(s.duty_webhook_url),
        "target": s.forge_target_name or "order-api",
    }


def h5_url(path: str = "/", query: dict[str, str] | None = None) -> str:
    """
    【业务】拼手机能打开的地址（值班台或批准页）。局域网 IP 来自 FORGE_PUBLIC_URL。
    【技术】base + path + query。钉钉卡上的链接都经这里。
    """
    base = get_settings().forge_public_url.rstrip("/")
    qs = urllib.parse.urlencode(query or {})
    return f"{base}{path}" + (f"?{qs}" if qs else "")


def register_pending(session_id: str) -> str:
    """
    【业务】发一张短通行证，钉钉链接才能对上「哪个会话在等批准」。
    【技术】16 位 hex 放进程字典；反向键 sid:{session_id} 方便按会话查。
            API 重启字典清空，还要靠 persist_pending 那张表。
    """
    token = uuid4().hex[:16]
    pending_tokens[token] = session_id
    pending_tokens[f"sid:{session_id}"] = token
    return token


def session_for_pending(token: str) -> str | None:
    """
    【业务】卡片 token 对回会话 id。
    【技术】只查内存。没有则 None，lookup_pending 再查库。
    """
    return pending_tokens.get(token)


def forget_pending(token: str) -> None:
    """
    【业务】这张批准卡用过了，内存里作废，防再点一次。
    【技术】同时删 token 和 sid: 反向键。
    """
    sid = pending_tokens.pop(token, None)
    if sid:
        pending_tokens.pop(f"sid:{sid}", None)


async def persist_pending(token: str, session_id: str, tool: str, arguments: str) -> None:
    """
    【业务】把通行证落到 Postgres，API 重启后手机卡还能对上会话和工具名。
    【技术】插 DutyPendingRow。失败只打日志，不挡推送（内存映射可能还在）。
    【注意】内存 interruptions 重启仍会丢，卡片可能提示过期，这是已知限制。
    """
    from app.db.session import get_session_factory
    from app.models.duty import DutyPendingRow

    factory = get_session_factory()
    async with factory() as db:
        db.add(
            DutyPendingRow(
                token=token,
                session_id=session_id,
                tool=(tool or "")[:80],
                arguments=(arguments or "")[:8000],
            )
        )
        try:
            await db.commit()
        except Exception:
            await db.rollback()
            log.warning("persist duty pending failed")


async def lookup_pending(db, token: str):
    """
    【业务】打开/点卡片时：这 token 还有效吗、是哪个会话。
    【技术】先 duty_pending 表，没有再内存。内存命中则临时拼一个只有 session_id 的行。
    """
    from app.models.duty import DutyPendingRow

    row = await db.get(DutyPendingRow, token)
    if row:
        return row
    sid = session_for_pending(token)
    if not sid:
        return None
    return DutyPendingRow(token=token, session_id=sid, tool="", arguments="")


async def consume_pending(db, token: str) -> None:
    """
    【业务】允许或拒绝已经生效，这张卡作废。
    【技术】forget_pending + delete 库行。调用方随后 commit。
    """
    from app.models.duty import DutyPendingRow

    forget_pending(token)
    row = await db.get(DutyPendingRow, token)
    if row:
        await db.delete(row)


_prompt_snap: dict | None = None
_prompt_snap_at = 0.0


def investigation_subject(alert: dict) -> dict:
    """按告警来源决定要打开的项目目录。知识库超时不能落到 order-api。"""
    from app.core.config import REPO_ROOT
    from app.services.supervisor import target_root

    service = str(alert.get("service") or "")
    blob = " ".join(
        [service, str(alert.get("title") or ""), str(alert.get("summary") or "")]
    )
    parent = REPO_ROOT.parent
    lowered = blob.lower()
    if "kb-qa" in lowered or "知识库" in blob or "ledger" in lowered:
        return {"kind": "ledger", "name": "Ledger", "workspace": parent / "ledger-kb"}
    if "roomcheck" in lowered or "客房" in blob:
        return {"kind": "roomcheck", "name": "RoomCheck", "workspace": parent / "roomcheck"}
    return {"kind": "order-api", "name": "order-api", "workspace": target_root()}


def format_investigation_prompt(alert: dict, state: dict | None = None) -> str:
    """
    【业务】把纸条翻译成给模型的第一段话：只修当前激活题、仓库在哪、金丝雀正文、
            先读文件再改、先测后重启。watchdog 和收件箱列表都会预生成。
    【技术】state 缺省则 snapshot；3 秒内复用，避免列表 40 条每条打一次 /canary。
    【是否主线】是。investigate 返回的 prompt、前端代发的第一条都来自它。
    """
    subject = investigation_subject(alert)
    if subject["kind"] != "order-api":
        workspace = subject["workspace"]
        parts = [
            f"你正在排查 {subject['name']}，不是 order-api 下单热修。",
            f"工作区必须是：{workspace}",
            "禁止读取或修改 order-api 的 bugs.py、app.py、active_bug.txt、tests/。那些文件和这次告警无关。",
            "只在上面这个工作区里用 read_file、grep_files、list_dir 定位，并用 write_file 修改该项目自己的文件。",
        ]
        if subject["kind"] == "ledger":
            parts.append(
                "Ledger 是知识库问答 Agent。相关目录：worker/app（LangGraph 检索与消费）、"
                "java-api（任务排队和超时上报）、frontend、corpus。超时通常是 worker 没在消费 RabbitMQ，或嵌入/重排失败。"
            )
        elif subject["kind"] == "roomcheck":
            parts.append("RoomCheck 是客房验收 Agent。只改这个仓库里的业务代码和测试。")
        parts.append(f"标题：{alert.get('title') or '未命名'}")
        if alert.get("service"):
            parts.append(f"服务：{alert['service']}")
        if alert.get("summary"):
            parts.append(f"报错摘要：{alert['summary']}")
        if alert.get("stack"):
            parts.append("报错日志：\n" + str(alert["stack"])[:6000])
        else:
            parts.append("报错日志：告警没有另附堆栈，以上摘要就是这次失败记录。")
        if alert.get("link"):
            parts.append(f"相关链接：{alert['link']}")
        parts.append("先说明要改的文件和原因，再做最小修改。不要重启 order-api，不要切 bug 题号。")
        return "\n".join(parts)

    from app.services.supervisor import snapshot, target_root

    global _prompt_snap, _prompt_snap_at
    if state is None:
        now = time.time()
        if _prompt_snap is not None and now - _prompt_snap_at < 3:
            state = _prompt_snap
        else:
            state = snapshot()
            _prompt_snap = state
            _prompt_snap_at = now
    workspace = str(target_root())
    n = state.get("active_bug") or "?"
    canary = state.get("canary") or {}
    parts = [
        "你是值班热修助手。先定位，再改代码。不要大重构，一次只修当前激活的那一个 bug。",
        f"业务仓库（工作区）在值班电脑：{workspace}",
        "关键文件：app.py（HTTP）、bugs.py（下单逻辑，十个互斥缺陷）、tests/test_orders.py、active_bug.txt（当前题号）。",
        f"当前激活 BUG-{n}。不要去改其它题的分支。",
        "DeepSeek 看不到运行中的进程内存，只能读这些文件、金丝雀响应、进程日志。请先 read_file / grep_files / 对照堆栈。",
        f"标题：{alert.get('title') or '未命名'}",
    ]
    if alert.get("service"):
        parts.append(f"服务：{alert['service']}")
    if alert.get("severity"):
        parts.append(f"级别：{alert['severity']}")
    if alert.get("summary"):
        parts.append(f"摘要：{alert['summary']}")
    if alert.get("link"):
        parts.append(f"金丝雀：{alert['link']}")
    if canary.get("body"):
        parts.append("金丝雀响应：\n" + str(canary.get("body"))[:4000])
    if alert.get("stack"):
        parts.append("堆栈/日志：\n" + str(alert["stack"])[:6000])
    log_tail = state.get("log_tail") or ""
    if log_tail:
        parts.append("业务进程日志尾：\n" + log_tail[-4000:])
    parts.append(
        "测试怎么做：run_tests 在业务仓库跑 pytest，检验的是磁盘上的代码，不会替换正在跑的 :8790 进程。"
        "禁止靠重启去“试一试”。先测通过，再提交。"
    )
    parts.append(
        "安全顺序（每步都要等人批准）："
        "1) 只读定位并说明文件/函数/原因；"
        "2) write_file 最小热修；"
        "3) run_tests；"
        "4) git_commit 然后 git_push origin（Gitee 已配）；"
        "5) 最后才 restart_service，让 :8790 加载新代码。"
        "金丝雀变绿才算这题修好。不要 force push。"
    )
    parts.append("回答里对代码和命令使用 markdown 代码块。")
    return "\n".join(parts)


def normalize_inbound(body: dict) -> dict:
    """
    【业务】不管对方叫 title 还是 alertname，最后都能插进 duty_alerts。
    【技术】有 alerts[] 走 Alertmanager；否则扁平字段 + 别名（message/text/log/url）。
            输出可 DutyAlertRow(**data)。截断防超列长。
    【谁调用】ingest_alert、demo_alert、watchdog.tick。
    """
    if isinstance(body.get("alerts"), list) and body["alerts"]:
        first = body["alerts"][0] if isinstance(body["alerts"][0], dict) else {}
        labels = first.get("labels") or {}
        ann = first.get("annotations") or {}
        return {
            "source": "alertmanager",
            "title": ann.get("summary") or labels.get("alertname") or "Alertmanager 告警",
            "summary": ann.get("description") or ann.get("summary") or "",
            "service": labels.get("service") or labels.get("job") or "",
            "severity": labels.get("severity") or "warning",
            "stack": json.dumps(first, ensure_ascii=False)[:8000],
            "link": first.get("generatorURL") or "",
        }
    return {
        "source": str(body.get("source") or "webhook")[:40],
        "title": str(body.get("title") or body.get("alertname") or "未命名告警")[:240],
        "summary": str(body.get("summary") or body.get("message") or body.get("text") or "")[:8000],
        "service": str(body.get("service") or "")[:120],
        "severity": str(body.get("severity") or "warning")[:32],
        "stack": str(body.get("stack") or body.get("log") or "")[:12000],
        "link": str(body.get("link") or body.get("url") or "")[:2000],
    }


async def notify_alert(alert: dict, session_id: str = "") -> list[str]:
    """
    【业务】新告警叫人：钉钉/企微/通用 webhook 打开值班台看这张纸条。
    【技术】拼 FORGE_PUBLIC_URL/?duty=1&alert=。不写库。输出实际成功的通道名。
    【谁调用】tick、ingest、demo、test_notify。
    """
    url = h5_url("/", {"duty": "1", "alert": str(alert.get("id") or ""), "session": session_id})
    title = f"Forge 值班 · {alert.get('title') or '新告警'}"
    desc = (alert.get("summary") or alert.get("stack") or "打开值班台排查")[:400]
    return await _fanout(title, desc, url)


async def notify_approval(session_id: str, tool: str, arguments: str) -> list[str]:
    """
    【业务】模型要 write_file 等变更时，手机再推「允许/拒绝」。
    【技术】register + persist token，链接到 /card 和 /card-decide。钉钉带两个按钮。
    【谁调用】chat._maybe_notify_approval、duty._resume_in_background。
    """
    token = register_pending(session_id)
    await persist_pending(token, session_id, tool, arguments)
    card = h5_url("/api/duty/card", {"token": token})
    allow = h5_url("/api/duty/card-decide", {"token": token, "approved": "1"})
    deny = h5_url("/api/duty/card-decide", {"token": token, "approved": "0"})
    title = f"Forge 值班 · 需要批准 {tool}"
    desc = (arguments or "")[:400] or "点允许继续，或打开值班台看 diff"
    return await _fanout(title, desc, card, buttons=[("允许", allow), ("拒绝", deny)])


async def _fanout(
    title: str,
    desc: str,
    url: str,
    buttons: list[tuple[str, str]] | None = None,
) -> list[str]:
    """
    【业务】同一条通知尽量发到所有已配通道；一个挂了不影响另一个。
    【技术】按 channel_status 逐个 try。都没配只打 info，演示仍可只用网页。
    """
    sent: list[str] = []
    status = channel_status()
    if status["wecom"]:
        try:
            await _send_wecom(title, desc, url)
            sent.append("wecom")
        except Exception as exc:
            log.warning("wecom notify failed: %s", exc)
    if status["dingtalk"]:
        try:
            await _send_dingtalk(title, desc, url, buttons)
            sent.append("dingtalk")
        except Exception as exc:
            log.warning("dingtalk notify failed: %s", exc)
    if status["webhook"]:
        try:
            await _send_generic(title, desc, url)
            sent.append("webhook")
        except Exception as exc:
            log.warning("webhook notify failed: %s", exc)
    if not sent:
        log.info("duty notify (no channel): %s %s", title, url)
    return sent


async def _wecom_access_token() -> str:
    """
    【业务】发企微卡片前要先拿到 access_token。
    【技术】进程内存缓存，过期前 60 秒再换。调企业微信 gettoken。
    """
    now = time.time()
    if _wecom_token["value"] and _wecom_token["exp"] > now + 60:
        return _wecom_token["value"]
    s = get_settings()
    async with httpx.AsyncClient(timeout=15) as client:
        res = await client.get(
            "https://qyapi.weixin.qq.com/cgi-bin/gettoken",
            params={"corpid": s.wecom_corp_id, "corpsecret": s.wecom_corp_secret},
        )
        res.raise_for_status()
        data = res.json()
    if data.get("errcode"):
        raise RuntimeError(data.get("errmsg") or "wecom token error")
    token = data["access_token"]
    _wecom_token["value"] = token
    _wecom_token["exp"] = now + int(data.get("expires_in") or 7200)
    return token


async def _send_wecom(title: str, desc: str, url: str) -> None:
    """
    【业务】给企微推一张「打开值班台」文本卡片。
    【技术】textcard + agentid。touser 默认 @all。errcode 非 0 抛错给 _fanout。
    """
    s = get_settings()
    token = await _wecom_access_token()
    body = {
        "touser": s.forge_duty_notify_user or "@all",
        "msgtype": "textcard",
        "agentid": int(s.wecom_agent_id) if str(s.wecom_agent_id).isdigit() else s.wecom_agent_id,
        "textcard": {
            "title": title[:128],
            "description": desc[:512],
            "url": url,
            "btntxt": "打开值班台",
        },
    }
    async with httpx.AsyncClient(timeout=15) as client:
        res = await client.post(
            "https://qyapi.weixin.qq.com/cgi-bin/message/send",
            params={"access_token": token},
            json=body,
        )
        res.raise_for_status()
        data = res.json()
    if data.get("errcode"):
        raise RuntimeError(data.get("errmsg") or "wecom send error")


def dingtalk_accepted(data: dict | None) -> bool:
    """【技术】钉钉返回 errcode==0 才算发出去。"""
    if not isinstance(data, dict) or "errcode" not in data:
        return False
    try:
        return int(data.get("errcode") or 0) == 0
    except (TypeError, ValueError):
        return False


def _dingtalk_url() -> str:
    """
    【业务】加签机器人要在 webhook 上带 timestamp 和 sign，否则钉钉拒收。
    【技术】HMAC-SHA256(timestamp + 换行 + secret)，与官方加签算法一致。
    """
    s = get_settings()
    hook = s.dingtalk_webhook
    secret = s.dingtalk_secret.strip()
    if not secret:
        return hook
    ts = str(round(time.time() * 1000))
    string_to_sign = f"{ts}\n{secret}"
    digest = hmac.new(secret.encode(), string_to_sign.encode(), hashlib.sha256).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(digest))
    sep = "&" if "?" in hook else "?"
    return f"{hook}{sep}timestamp={ts}&sign={sign}"


async def _send_dingtalk(
    title: str, desc: str, url: str, buttons: list[tuple[str, str]] | None = None
) -> None:
    """
    【业务】钉钉 actionCard：告警带「打开」，批准带允许/拒绝。
    【技术】局域网链接常被钉钉判不安全，失败则降级成纯文本让人复制 URL。
    """
    card: dict = {
        "title": title,
        "text": f"### {title}\n\n{desc}\n\n[打开]({url})",
    }
    if buttons:
        card["btnOrientation"] = "1"
        card["btns"] = [{"title": name[:20], "actionURL": href} for name, href in buttons[:2]]
    else:
        card["singleTitle"] = "打开值班台"
        card["singleURL"] = url
    payload = {"msgtype": "actionCard", "actionCard": card}
    async with httpx.AsyncClient(timeout=20) as client:
        res = await client.post(_dingtalk_url(), json=payload)
        data = res.json() if res.content else {}
        if dingtalk_accepted(data):
            return
        errmsg = str(data.get("errmsg") or data)
        # Private LAN links are often rejected as unsafe. Fall back to copy-paste text.
        text = (
            f"{title}\n\n{desc}\n\n"
            f"请用系统浏览器打开（钉钉内置浏览器可能打不开局域网）：\n{url}"
        )[:2000]
        res2 = await client.post(
            _dingtalk_url(),
            json={"msgtype": "text", "text": {"content": text}},
        )
        data2 = res2.json() if res2.content else {}
        if not dingtalk_accepted(data2):
            raise RuntimeError(data2.get("errmsg") or errmsg or str(data2))


async def _send_generic(title: str, desc: str, url: str) -> None:
    """
    【业务】自己的 webhook（不是钉钉/企微）也收一份同样的标题和链接。
    【技术】POST DUTY_WEBHOOK_URL，JSON：title/text/url/source=forge-duty。
    """
    s = get_settings()
    async with httpx.AsyncClient(timeout=15) as client:
        res = await client.post(
            s.duty_webhook_url,
            json={"title": title, "text": desc, "url": url, "source": "forge-duty"},
        )
        res.raise_for_status()


def alert_public(row) -> dict:
    return {
        "id": row.id,
        "title": row.title,
        "summary": row.summary,
        "service": row.service,
        "severity": row.severity,
        "source": row.source,
        "stack": row.stack,
        "link": row.link,
        "status": row.status,
        "session_id": row.session_id,
        "created_at": row.created_at.isoformat() if row.created_at else "",
    }


async def persist_alert(body: dict) -> dict:
    """Insert a duty alert. A2A ingest-incident de-dupes open rows with the same title."""
    from sqlalchemy import select

    from app.db.session import get_session_factory
    from app.models.duty import DutyAlertRow

    data = normalize_inbound(body)
    factory = get_session_factory()
    async with factory() as db:
        if data.get("source") == "a2a" and data.get("title"):
            existing = (
                await db.execute(
                    select(DutyAlertRow).where(
                        DutyAlertRow.source == "a2a",
                        DutyAlertRow.title == data["title"],
                        DutyAlertRow.status.in_(("open", "investigating", "acked")),
                    )
                )
            ).scalars().first()
            if existing:
                return alert_public(existing)
        row = DutyAlertRow(**data)
        db.add(row)
        await db.commit()
        await db.refresh(row)
        public = alert_public(row)
    asyncio.create_task(notify_alert(public))
    return public
