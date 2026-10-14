"""
Agent 运行时：值班对话真正跑模型的地方。

业务故事：
    点排查、网页把任务说明发到 chat.send_message 之后，本文件 start_prompt 才叫模型开口。
    OpenAI Agents SDK 的 Runner 负责「想一想 → 调工具 → 再想」。没有 LangGraph。
    要改 bugs.py 时 SDK 会停住；人点允许后 start_resume 接着跑。
    字不是等说完再给网页，而是 _produce 边收边推。刷新页面任务还在服务器上跑。

本文件不管收件箱、不看金丝雀、不执行 write_file（工具在 tools/）。
这里管：选模型、组 Agent、开跑/续跑、把事件推给网页、把结构化历史写入 agent_states。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agents import Agent, Runner, set_tracing_disabled
from agents.extensions.models.litellm_model import LitellmModel
from agents.items import ItemHelpers
from agents.result import RunResult, RunResultStreaming

from app.core.config import get_settings
from app.db.session import get_session_factory
from app.models.agent_state import AgentStateRow
from app.services.llm_prefs import preferred_model
from app.services.messages import add_message
from app.services.project_notes import load_project_notes
from app.tools import ForgeContext, all_tools, readonly_review_tools

set_tracing_disabled(True)
log = logging.getLogger("forge.agent")

INSTRUCTIONS = """你是 Forge，值班机上的本地 coding agent。

规则：
- 只在当前选定的工作区内活动，禁止访问父目录。
- 读代码优先用 grep_files、glob_files、list_dir、read_file、search_code。
- 查看改动时用只读的 git_status / git_diff / git_log / git_branch。
- 监控中的 order-api 热修：先诊断，再 write_file，再调用 review_hotfix（只读评审子 Agent），通过后再 run_tests（磁盘上的 pytest）。测试不能代替正在跑的 :8790 进程。测试通过后：git_commit、git_push，然后 restart_service。每一步变更都要等人批准。
- 若用户消息写明正在排查 Ledger 知识库问答 Agent 或 RoomCheck，工作区就是消息里给出的那个目录。只改那个项目的文件，禁止去改 order-api 的 bugs.py、app.py、active_bug.txt。
- write_file 之后、run_tests / restart_service 之前必须调用 review_hotfix。若评审驳回，按原因改完再评，不要直接重启。
- 禁止把 restart_service 当成「试一试修复」的手段。禁止 force push。
- 工具会在代码里拒绝，即使你调用了也一样：工作区外路径、C 盘、绝对路径、.env、git reset --hard、force push。热修范围（监控中的 order-api）还会拒绝 tests/、README、AGENTS.md、active_bug.txt；只能写 bugs.py 和 app.py。若工具返回「拒绝」，把原因告诉用户，不要再尝试绕过。
- 会改仓库的 git_* 工具跑在宿主上，这样才能用值班机已有的 git 凭据。
- plan 模式只能读和搜索。给出编号计划，不要改文件。
- build 模式用 write_file 改文件、用 delete_file 删文件、用 run_tests 验收。
- bash 和 run_tests：order-api 带有 .forge-host-tests，pytest 在宿主跑；其他仓库走 Linux 沙箱，工作区挂在 /workspace。
- Python、diff、shell 命令放在 markdown 代码块里。
- 客房验收业务 Agent（RoomCheck）软件故障：用 a2a_roomcheck 经 A2A 调 room-canary / room-inspect，禁止 curl 对端私有 HTTP。缺茶杯是业务不合格，不要当成 Forge 热修。
- 知识库问答交给同级的 kb-qa-agent：用 a2a_kb，skill 用 ask 或 canary。不要用 curl 打 8792。金丝雀失败由值班巡检写入收件箱并推送，不要当成热修。
- 回答简短，改了哪个文件就写精确路径。
- 若用户消息以 [当前编辑器打开：…] 开头，该文件已在屏幕上，相关时先读它。
"""

REVIEWER_INSTRUCTIONS = """你是 Forge 的只读热修评审员，不是作者。

规则：
- 只能读代码和 git_status / git_diff。禁止改文件、跑测试、提交、重启。
- 检查改动是否最小，是否只动了 bugs.py / app.py，有没有改 tests/、README、AGENTS.md、active_bug.txt。
- 用几句话给出结论：通过 或 驳回。驳回必须写明原因。
- 不要自己做热修，不要建议 force push。
"""


def _litellm(model_id: str, api_key: str | None, base_url: str | None = None) -> LitellmModel:
    """
    【业务】SDK 不直接认「DeepSeek / 千问」这些名字，要先包成 LitellmModel，Runner 才能发请求。
    【什么时候用】已经选定模型 id、并拿到对应 Key 之后，组最终模型对象。
    【谁调用】_model_for；resolve_model 在走 DeepSeek/Claude 默认分支时也会直接调。
    【参数】
        model_id：厂商/模型名，如 deepseek/deepseek-reasoner。
        api_key：这个厂商的密钥，可空（空则交给 LiteLLM 自己找，热修里一般会传入）。
        base_url：可选，兼容 OpenAI 的网关地址（Cursor、千问有时要）。
    【里面调用】LitellmModel(...)：SDK 扩展，真正发 HTTP 的那一层。
    【是否主线】帮手。选错模型热修会 401，但值班员平时感觉不到这个函数。
    """
    return LitellmModel(model=model_id, api_key=api_key or None, base_url=base_url or None)


def resolve_model(explicit: str | None = None) -> Any:
    """
    【业务】这一轮值班对话到底用哪个大模型。侧栏锁了模型就用锁的；否则用你在设置里
            记的首选，再否则用 .env 里 FORGE_MODEL，再否则看哪个 Key 配了（DeepSeek 优先）。
            一个 Key 都没有会报错，提醒去填 .env，避免模型跑到一半才 401。
    【什么时候用】每次 build_agent（发消息、点允许续跑都会新建 Agent）。
    【谁调用】build_agent。
    【参数】explicit：这次会话锁定的模型 id，例如 deepseek/deepseek-reasoner；空则走默认链。
    【里面调用】
        get_settings()：读 .env 配置。
        preferred_model()：读 data/preferred_model.txt。
        _qualify：短名补厂商前缀。
        _model_for / _litellm：组出 SDK 能用的模型对象。
    【是否主线】是。热修演示常用 DeepSeek Reasoner，就是这里选出来的。
    """
    settings = get_settings()
    chosen = (explicit or preferred_model() or settings.forge_model).strip()
    if chosen:
        model_id = chosen if "/" in chosen or chosen.startswith("gpt-") else _qualify(chosen)
        return _model_for(model_id, settings)
    if settings.deepseek_api_key:
        return _litellm("deepseek/deepseek-chat", settings.deepseek_api_key)
    if settings.zhipuai_api_key or settings.zai_api_key:
        return _model_for("zhipuai/glm-4-flash", settings)
    if settings.dashscope_api_key:
        return _model_for("dashscope/qwen-plus", settings)
    if settings.xai_api_key:
        return _model_for("xai/grok-3", settings)
    if settings.cursor_api_key and settings.cursor_api_base:
        return _model_for("cursor/grok-4", settings)
    if settings.anthropic_api_key:
        return _litellm("anthropic/claude-sonnet-4-5", settings.anthropic_api_key)
    if settings.openai_api_key:
        return "gpt-4.1"
    raise RuntimeError(
        "Set DEEPSEEK_API_KEY, ZHIPUAI_API_KEY, DASHSCOPE_API_KEY, XAI_API_KEY, "
        "ANTHROPIC_API_KEY, or OPENAI_API_KEY in .env"
    )


def _qualify(name: str) -> str:
    """
    【业务】有人只填 deepseek-chat、不写 deepseek/。LiteLLM 要带厂商前缀才能路由到正确接口。
    【什么时候用】resolve_model 发现名字里没有 /、也不是 gpt- 开头时。
    【谁调用】resolve_model。
    【参数】name：用户或配置里写的模型名，短名或已经带前缀都可以。
    【里面调用】无（只做字符串判断）。
    【是否主线】帮手。
    """
    if name.startswith(("gpt-", "o1", "o3", "o4")):
        return name
    if name.startswith("claude"):
        return f"anthropic/{name}"
    if name.startswith("deepseek"):
        return f"deepseek/{name}" if not name.startswith("deepseek/") else name
    if name.startswith("glm-"):
        return f"zhipuai/{name}"
    if name.startswith("qwen"):
        return f"dashscope/{name}"
    if name.startswith("grok"):
        return f"xai/{name}"
    return name


def _key_for(model_id: str, settings: Any) -> str:
    """
    【业务】deepseek 开头用 DEEPSEEK_API_KEY，千问用 DASHSCOPE，智谱用 ZHIPUAI，以此类推。
            选错 Key，请求会打到别的厂商或直接 401。
    【什么时候用】已经确定 model_id，要取出对应密钥。
    【谁调用】_model_for。
    【参数】
        model_id：已规范化的 id，如 deepseek/deepseek-reasoner。
        settings：get_settings() 的结果，里面是各家 Key。
    【里面调用】无。
    【是否主线】帮手。
    """
    if model_id.startswith("deepseek"):
        return settings.deepseek_api_key
    if model_id.startswith("cursor/"):
        return settings.cursor_api_key
    if model_id.startswith("xai/") or model_id.startswith("grok"):
        return settings.xai_api_key
    if model_id.startswith(("zhipu", "zai/", "glm-")):
        return settings.zhipuai_api_key or settings.zai_api_key
    if model_id.startswith("dashscope") or model_id.startswith("qwen"):
        return settings.dashscope_api_key
    if model_id.startswith(("anthropic/", "claude")):
        return settings.anthropic_api_key
    return settings.openai_api_key


def _model_for(model_id: str, settings: Any) -> LitellmModel:
    """
    【业务】模型名已经定了，这里检查 Key 在不在，再交给 _litellm 打包。
            Cursor 的模型要改写成 openai/ 前缀，走它的兼容网关。
            Key 缺失立刻用中文报错，不要等 Runner 跑起来才失败。
    【什么时候用】resolve_model 已经选出具体 id 之后。
    【谁调用】resolve_model。
    【参数】model_id：规范化 id；settings：配置对象。
    【里面调用】_key_for 取密钥；_litellm 生成模型对象。
    【是否主线】帮手。
    """
    if model_id.startswith("cursor/"):
        if not (settings.cursor_api_key and settings.cursor_api_base):
            raise RuntimeError("Cursor Grok 需要在项目根目录 .env 填写 CURSOR_API_KEY 和 CURSOR_API_BASE")
        name = model_id.split("/", 1)[1]
        return _litellm(f"openai/{name}", settings.cursor_api_key, settings.cursor_api_base)
    key = _key_for(model_id, settings)
    if not key:
        raise RuntimeError(f"模型 {model_id} 还没有对应的 API Key，打开项目根目录 .env 补上后重启 API")
    base = ""
    if model_id.startswith("dashscope") or model_id.startswith("qwen"):
        base = settings.dashscope_api_base
    return _litellm(model_id, key, base or None)


def make_forge_context(
    session_id: str,
    workspace: Path,
    mode: str,
    actor: str = "local",
    model_id: str | None = None,
    role: str = "",
    auto_approve: bool | None = None,
    allow_git_push: bool = True,
    original_intent: str = "",
) -> ForgeContext:
    """
    【业务】每个工具函数第一参能读到的「这次值班上下文」：在哪个文件夹干活、能不能自动批准、
            热修时只能改 bugs.py/app.py（write_scope=hotfix）。不是给模型看的提示词，是给 Python 工具看的。
    【什么时候用】start_prompt / start_resume 马上要 Runner.run_streamed 之前。
    【谁调用】start_prompt、start_resume。
    【参数】
        session_id：这次工作台对话 id。
        workspace：工具允许读写的根目录（一般是 order-api）。
        mode：plan 只能读；build 才能写文件（热修是 build）。
        actor：谁在操作，登录用户名，写入工具审计用。
        model_id：当前模型名，给工具侧记录。
        role：admin / engineer 等；管理员且开了自动批配置时 auto_approve 会变 True。
        auto_approve：评测脚本会传 True 跳过人工点允许；值班台一般是 None。
        allow_git_push：是否允许 git_push 工具真推远程。
        original_intent：本轮用户原话，给工具 Guardrail 做意图一致性。
    【里面调用】
        get_settings()：看管理员是否自动批准。
        is_target_workspace(workspace)：当前目录是不是被监控的 order-api，是则热修白名单。
        ForgeContext(...)：tools 里定义的数据包。
    【是否主线】是。没有 hotfix 范围，模型可能去改 tests/ 或题号文件。
    """
    from app.core.config import get_settings

    approve = bool(auto_approve)
    if auto_approve is None and role == "admin" and get_settings().forge_admin_auto_approve:
        approve = True
    write_scope = "workspace"
    try:
        from app.services.supervisor import is_target_workspace

        if is_target_workspace(workspace):
            write_scope = "hotfix"
    except Exception:
        write_scope = "workspace"
    return ForgeContext(
        session_id=session_id,
        workspace=workspace,
        mode=mode,
        actor=actor,
        model=model_id or "",
        role=role,
        auto_approve=approve,
        allow_git_push=allow_git_push,
        write_scope=write_scope,
        original_intent=(original_intent or "")[:4000],
    )


def build_agent(
    mode: str,
    model_id: str | None = None,
    workspace: Path | None = None,
) -> Agent[ForgeContext]:
    """
    【业务】组出一个「值班助手」给 SDK：系统提示（先测后重启、只能改 bugs.py/app.py）、
            全部工具（读文件、写文件、测试、git、重启）、以及这一次用的模型。
            每次发消息/续跑都新建，避免两个会话共用一份提示。
    【什么时候用】start_prompt、start_resume 里，Runner.run_streamed 之前。
    【谁调用】start_prompt、start_resume。
    【参数】
        mode：拼进提示「当前模式：build/plan」。
        model_id：交给 resolve_model。
        workspace：用来读仓库里的 AGENTS.md / FORGE.md，没有则不加项目说明。
    【里面调用】
        load_project_notes：读项目笔记前 6KB。
        resolve_model：选出模型对象。
        all_tools()：17 个 @function_tool。
        readonly_review_tools()：只读评审子 Agent 的工具面。
        Agent(...)：SDK 的 Agent 类；reviewer.as_tool 挂成 review_hotfix。
    【是否主线】是。模型能调哪些工具、热修顺序写在哪，都在这里挂上。
    """
    extra = f"\n当前模式：{mode}。"
    notes = load_project_notes(workspace) if workspace else ""
    if notes:
        extra += "\n\n项目说明（AGENTS.md / FORGE.md）：\n" + notes
    model = resolve_model(model_id)
    from app.services.tool_audit import ForgeToolAuditHooks

    hooks = ForgeToolAuditHooks()
    reviewer = Agent[ForgeContext](
        name="hotfix_reviewer",
        instructions=REVIEWER_INSTRUCTIONS,
        model=model,
        tools=readonly_review_tools(),
        hooks=hooks,
    )
    return Agent[ForgeContext](
        name="Forge",
        instructions=INSTRUCTIONS + extra,
        model=model,
        hooks=hooks,
        tools=all_tools()
        + [
            reviewer.as_tool(
                tool_name="review_hotfix",
                tool_description=(
                    "只读评审当前热修。传入刚改的文件和修改意图；"
                    "子 Agent 会读 diff 和相关代码，判断是否越权改测试或题号。"
                    "返回通过或驳回及原因。write_file 之后、run_tests / restart_service 之前调用。"
                ),
                max_turns=8,
            )
        ],
    )


def model_label() -> str:
    """
    【业务】给界面/健康检查看「现在默认模型叫什么」，不真的去调 API。
            没有 Key 时返回 (no key)，方便你知道还没配。
    【什么时候用】列会话、健康检查、duty 展示模型名。
    【谁调用】routers 里需要展示默认模型的地方（如 duty.model_label）。
    【参数】无。
    【里面调用】get_settings、preferred_model。
    【是否主线】旁路。热修闭环不依赖这个展示名。
    """
    settings = get_settings()
    if preferred_model():
        return preferred_model()
    if settings.forge_model.strip():
        return settings.forge_model.strip()
    if settings.deepseek_api_key:
        return "deepseek/deepseek-chat"
    if settings.zhipuai_api_key or settings.zai_api_key:
        return "zhipuai/glm-4-flash"
    if settings.dashscope_api_key:
        return "dashscope/qwen-plus"
    if settings.xai_api_key:
        return "xai/grok-3"
    if settings.anthropic_api_key:
        return "anthropic/claude-sonnet-4-5"
    if settings.openai_api_key:
        return "gpt-4.1"
    return "(no key)"


def _item_text(item: Any) -> str:
    """
    【业务】SDK 的一条助手消息不是普通字符串，正文藏在几层对象里。这里抽出给人看的整段字。
    【什么时候用】serialize_stream_event 遇到「一条助手消息已经拼完」（message_output_created）。
    【谁调用】serialize_stream_event。
    【参数】item：SDK 的 MessageOutputItem（或长得像它的对象）。
    【里面调用】ItemHelpers.text_message_output：SDK 官方抽文本；失败则 str(item.content)。
    【是否主线】帮手。网页一个字一个字出，主要靠 text_delta，这个是整句完成时的全文。
    """
    try:
        return ItemHelpers.text_message_output(item)
    except Exception:
        return str(getattr(item, "content", "") or "")


# 跑完后工作区文件可能变了，iter_stream_events 要多推一条 fs_changed 给文件树。
# 不是 HITL 名单（git_push / run_tests / restart_service 要批准，但不在这里）。
MUTATING_TOOLS = {"write_file", "delete_file", "bash", "git_checkout", "git_commit"}


def path_from_tool_args(tool: str, arguments: str) -> str:
    """
    【业务】write_file / delete_file 的参数是 JSON 字符串。网页刷新文件树需要知道动的是哪个相对路径。
            bash / git 没有这种 path，返回空串。
    【什么时候用】拼 fs_changed 事件时。
    【谁调用】fs_changed_event。
    【参数】
        tool：工具名，只有 write_file / delete_file 才会去抠 path。
        arguments：工具参数原文，如 {"path":"bugs.py","content":"..."}。
    【里面调用】json.loads 把参数变成字典。
    【是否主线】帮手。热修改 bugs.py 后左边文件树能亮，靠这里抠出的路径。
    """
    if tool not in {"write_file", "delete_file"}:
        return ""
    try:
        data = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        return ""
    path = data.get("path")
    if not path:
        return ""
    rel = str(path).replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    return rel.lstrip("/")


def fs_changed_event(tool: str, arguments: str) -> dict[str, Any]:
    """
    【业务】磁盘上的文件已经变了，造一条给网页的通知：刷新左边文件树，必要时打开或关掉标签。
            这不是模型说的话，是 Forge 自己补的。
    【什么时候用】iter_stream_events 看到 tool_output，且刚才的工具属于 MUTATING_TOOLS。
    【谁调用】iter_stream_events。
    【参数】tool：刚跑完的工具名；arguments：那次调用的参数（用来抠 path）。
    【里面调用】path_from_tool_args 取相对路径。
    【是否主线】是。没有它，模型改完 bugs.py，文件树还停在旧样子。
    """
    op = "delete" if tool == "delete_file" else "write" if tool == "write_file" else "bash"
    return {
        "type": "fs_changed",
        "tool": tool,
        "path": path_from_tool_args(tool, arguments),
        "op": op,
    }


async def iter_stream_events(result: RunResultStreaming) -> AsyncIterator[dict[str, Any]]:
    """
    【业务】SDK 吐出的是原始事件对象。这里翻译成网页/_produce 能用的小字典：
            几个字、要调哪个工具、工具结果、整句说完；改磁盘的工具再多补一条 fs_changed。
            SDK 的 tool_output 有时不带工具名，所以先记住上一次 tool_called。
    【什么时候用】_produce 一开始，整轮流式生成期间。
    【谁调用】_produce。
    【参数】result：start_prompt / start_resume 里 Runner.run_streamed 的返回值，自带 stream_events()。
    【里面调用】
        result.stream_events()：SDK 水龙头，模型一边跑一边冒事件。
        serialize_stream_event：过滤、翻成字典，垃圾事件变 None。
        fs_changed_event：变更工具跑完后通知刷新文件树。
    【是否主线】是。没有它 _produce 只能面对 SDK 原始对象，网页画不了。
    """
    last_tool = ""
    last_args = ""
    async for ev in result.stream_events():
        serialized = serialize_stream_event(ev)
        if not serialized:
            continue
        if serialized.get("type") == "tool_called":
            last_tool = str(serialized.get("tool") or "")
            last_args = str(serialized.get("arguments") or "")
        elif serialized.get("type") == "tool_output":
            serialized["tool"] = serialized.get("tool") or last_tool
            serialized["arguments"] = last_args
        yield serialized
        if serialized.get("type") == "tool_output" and last_tool in MUTATING_TOOLS:
            yield fs_changed_event(last_tool, last_args)


def serialize_stream_event(ev: Any) -> dict[str, Any] | None:
    """
    【业务】安检：只放行「又吐了几个字」和「一件完整的事」（要调工具 / 工具结束 / 整句完成）。
            其它内部碎片丢掉。不调用 LLM，不执行工具，也不发 approval_needed。
    【什么时候用】iter_stream_events 每从 SDK 拿到一件 ev 就过一次。
    【谁调用】iter_stream_events。
    【参数】ev：SDK 的 StreamEvent。type 常见 raw_response_event（生增量）或 run_item_stream_event（熟事项）。
    【里面调用】
        getattr：安全取字段，缺了不崩。
        _item_text：整句完成时抽出全文。
    【是否主线】是。text_delta / tool_called / tool_output 都从这里整形。
    """
    if ev.type == "raw_response_event":
        data = ev.data
        kind = getattr(data, "type", "")
        delta = getattr(data, "delta", None)
        if kind in {"response.output_text.delta", "response.reasoning_text.delta"} and delta:
            return {"type": "text_delta", "text": delta}
        return None
    if ev.type == "run_item_stream_event":
        name = ev.name
        item = ev.item
        payload: dict[str, Any] = {"type": name}
        if name == "tool_called":
            raw = getattr(item, "raw_item", None)
            payload["tool"] = getattr(raw, "name", None) or getattr(item, "name", "")
            payload["arguments"] = getattr(raw, "arguments", "") or ""
        elif name == "tool_output":
            payload["output"] = str(getattr(item, "output", "") or "")[:8000]
            raw = getattr(item, "raw_item", None)
            payload["tool"] = getattr(raw, "name", "")
        elif name == "message_output_created":
            payload["text"] = _item_text(item)
        return payload
    return None


def _strip_nuls(value: Any) -> Any:
    """
    【业务】Postgres 存不下字符串里的空字符 \\x00。模型偶尔会吐出来，不剥掉 agent_states 写入会失败。
    【什么时候用】历史要 JSON 入库之前（_jsonable 内部）。
    【谁调用】_jsonable；自己递归处理列表/字典。
    【参数】value：任意一层（字符串、列表、字典或其它）。
    【里面调用】自己。不是字典/列表/字符串则原样返回。
    【是否主线】帮手。不剥 NUL，歇脚点落库可能整段失败。
    """
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, list):
        return [_strip_nuls(v) for v in value]
    if isinstance(value, dict):
        return {str(k).replace("\x00", ""): _strip_nuls(v) for k, v in value.items()}
    return value


def _jsonable(items: list[Any]) -> list[Any]:
    """
    【业务】SDK 的 item 里可能有不能直接给 Postgres JSONB 的对象。先 dumps 再 loads 变成纯 JSON，再剥空字符。
    【什么时候用】save_agent_state 写入前。
    【谁调用】save_agent_state。
    【参数】items：即将存进 agent_states.input_items 的列表。
    【里面调用】json.dumps / json.loads、_strip_nuls。
    【是否主线】帮手。
    """
    return _strip_nuls(json.loads(json.dumps(items, default=str)))


UNANSWERED_TOOL = (
    "Tool was not executed because approval expired or the API restarted. "
    "Continue without this result."
)


def close_unanswered_tool_calls(items: list[Any]) -> list[Any]:
    """
    【业务】模型要 write_file 时历史里会留下一次「调用」，人还没点允许就重启后端，
            这次调用没有「结果」。再送给 DeepSeek/OpenAI 会拒聊。
            这里不替你改文件，只在缺口后补一句「这次没执行成」，让对话能继续。
    【什么时候用】
        start_prompt 再开跑前；_produce 写入 agent_states 前；
        start_resume 发现批准对象已经没了（过期）时。
    【谁调用】start_prompt、_produce、start_resume（失效分支）。
    【参数】items：结构化历史 input_items（格式 A：function_call / function_call_output；
            格式 B：assistant.tool_calls / role=tool）。不是 messages 气泡。
    【里面调用】无其它业务函数。先扫已有结果进 answered，再给缺结果的调用补 UNANSWERED_TOOL。
    【是否主线】是。后端重启后再说「继续」，靠它避免模型接口报错。
    """
    answered: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "function_call_output" and item.get("call_id"):
            answered.add(str(item["call_id"]))
        if item.get("role") == "tool" and item.get("tool_call_id"):
            answered.add(str(item["tool_call_id"]))
        for call in item.get("tool_calls") or []:
            if isinstance(call, dict) and call.get("id") and item.get("role") == "tool":
                answered.add(str(call["id"]))
    out: list[Any] = []
    for item in items:
        out.append(item)
        if not isinstance(item, dict):
            continue
        if item.get("type") == "function_call":
            cid = str(item.get("call_id") or "")
            if cid and cid not in answered:
                out.append({"type": "function_call_output", "call_id": cid, "output": UNANSWERED_TOOL})
                answered.add(cid)
        if item.get("role") == "assistant":
            for call in item.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                cid = str(call.get("id") or "")
                if cid and cid not in answered:
                    out.append({"role": "tool", "tool_call_id": cid, "content": UNANSWERED_TOOL})
                    answered.add(cid)
    return out


async def load_input_items(session_id: str) -> list[Any]:
    """
    【业务】从数据库把「给模型用的工作记忆」读回来。刷新看聊天读 messages；续跑模型读这张表。
            新会话没有行，返回空列表。
    【什么时候用】内存里 input_items 还是空的（常见：uvicorn 刚重启，你在旧会话说「继续」）；
            或点允许时内存里根本没有 Runtime。
    【谁调用】start_prompt（rt.input_items 为空时）；start_resume（rt is None 时）。
    【参数】session_id：工作台会话 id，等于 agent_states 的主键。
    【里面调用】
        get_session_factory()：自己开一扇数据库窗（不走 FastAPI 的 get_db）。
        db.get(AgentStateRow, session_id)：按主键取那一行。
    【是否主线】是。没有它，重启后模型不记得已经读过 bugs.py。
    """
    factory = get_session_factory()
    async with factory() as db:
        row = await db.get(AgentStateRow, session_id)
        if row and isinstance(row.input_items, list):
            return list(row.input_items)
    return []


async def _safe_save(
    session_id: str,
    items: list[Any],
    pending_tool: str = "",
    pending_arguments: str = "",
) -> None:
    """
    【业务】尽量把当前工作记忆写入 agent_states。数据库抖了只记日志，不要让网页收不到
            「等你批准 / 已停止 / 说完了」。写失败的代价是重启后续聊可能缺这一段。
    【什么时候用】_produce 里这一轮已经停住：弹出批准、你点了停止、或整轮说完。
            不是每个字、每次工具调用都存。
    【谁调用】只有 _produce（三处）。
    【参数】
        session_id：哪一次对话。
        items：补过洞的 input_items。
        pending_tool / pending_arguments：卡在 HITL 时是工具名和参数；其它时候默认空。
    【里面调用】save_agent_state。异常只 log.exception。
    【是否主线】是。歇脚点落库走这里。
    """
    try:
        await save_agent_state(session_id, items, pending_tool, pending_arguments)
    except Exception:
        log.exception("save_agent_state failed session=%s", session_id)


async def save_agent_state(
    session_id: str,
    items: list[Any],
    pending_tool: str = "",
    pending_arguments: str = "",
) -> None:
    """
    【业务】每个会话一行 agent_states：input_items 给模型下次续跑；
            pending_tool / pending_arguments 给刷新后的网页重画「允许 write_file 吗」。
            没有行就插入，有行就更新。
    【什么时候用】_safe_save 成功路径；start_resume 发现批准过期时也会直接调（把补过的历史存回去）。
    【谁调用】_safe_save；start_resume 失效分支。
    【参数】同 _safe_save。items 先经 _jsonable 才能进 JSONB。
    【里面调用】get_session_factory、_jsonable、db.get / db.add / commit。
    【是否主线】是。和 messages 并行：这边是模型记忆，那边是给人看的气泡。
    """
    factory = get_session_factory()
    payload = _jsonable(items)
    pending_tool = (pending_tool or "").replace("\x00", "")
    pending_arguments = (pending_arguments or "").replace("\x00", "")
    async with factory() as db:
        row = await db.get(AgentStateRow, session_id)
        if row is None:
            db.add(
                AgentStateRow(
                    session_id=session_id,
                    input_items=payload,
                    pending_tool=pending_tool,
                    pending_arguments=pending_arguments,
                )
            )
        else:
            row.input_items = payload
            row.pending_tool = pending_tool
            row.pending_arguments = pending_arguments
        await db.commit()


@dataclass
class SessionRuntime:
    """
    【业务】不是函数，是一本「这次对话此刻在服务器内存里跑得怎么样」的草稿。
            数据库 sessions 行证明对话存在；这本证明这一轮 Agent 正在本进程里跑或等批准。
            uvicorn 重启后整本消失，历史靠 agent_states，批准对象没了。
    【什么时候用】_ensure_runtime 发现字典里还没有这本时 new 一份。之后大家都改同一本。
    【谁调用】只有 _ensure_runtime 会 SessionRuntime(...)。
    【字段】
        result：SDK 这一轮的对象，批准 interruptions 在这里。卡批准时不能清掉。
        input_items：当前喂给模型的结构化历史。
        user_cancel：你点了停止。
        task：后台 _produce 任务。
        subscribers：几个网页队列在听。
        replay / seq：已推事件和序号，刷新用 after= 续看。
        status：idle / running / approval / done / cancelled / error。
    【是否主线】是。send_message、点允许、刷新续订都围着这本转。
    """

    result: RunResult | RunResultStreaming | None = None
    input_items: list[Any] = field(default_factory=list)
    user_cancel: bool = False
    task: asyncio.Task | None = None
    subscribers: list[asyncio.Queue] = field(default_factory=list)
    replay: list[dict[str, Any]] = field(default_factory=list)
    seq: int = 0
    status: str = "idle"
    original_intent: str = ""


# session_id → 进程内 Runtime。API 重启后清空，历史靠 Postgres 恢复。
runtimes: dict[str, SessionRuntime] = {}

STALE_APPROVAL = (
    "批准已失效：API 重启后，内存里的批准对象没了。"
    "不要再点允许。把上一句话再发一遍即可，DeepSeek Pro 可以继续用。"
)

TERMINAL = frozenset({"done", "cancelled", "error", "approval_needed"})


def _runtime(session_id: str) -> SessionRuntime | None:
    """
    【业务】问：这个会话在本进程里有没有正在跑的那本草稿？没有就 None，不新建。
    【什么时候用】判断是不是已在跑、点停止、网页订阅事件、看批准还在不在。
    【谁调用】is_active、run_status、has_live_approval、cancel_run、subscribe_events。
    【参数】session_id：工作台会话 id，对应 runtimes 字典的键。
    【里面调用】runtimes.get。和 _ensure_runtime 相对：那边没有就建一本。
    【是否主线】帮手，但主线接口都会间接用到。
    """
    return runtimes.get(session_id)


def is_active(session_id: str) -> bool:
    """
    【业务】这一轮是不是还在出字，或弹窗还在等你点允许。是的话不要再 start_prompt，
            否则同一句排查稿会跑两遍。
    【什么时候用】send_message 落气泡、开模型之前。
    【谁调用】chat.send_message；start_prompt 自己也会再问一次。
    【参数】session_id：哪一次对话。
    【里面调用】_runtime。True 条件：status 是 running/approval，或后台 task 还没结束。
    【是否主线】是。防止连点发送。
    """
    rt = _runtime(session_id)
    if not rt:
        return False
    if rt.status in {"running", "approval"}:
        return True
    return bool(rt.task and not rt.task.done())


def run_status(session_id: str) -> str:
    """
    【业务】给侧栏/会话详情一个状态字：idle（空闲）或 running / approval 等。没有草稿本当 idle。
    【什么时候用】GET 会话信息、列表展示「还在跑吗」。
    【谁调用】sessions 路由拼 SessionOut 时。
    【参数】session_id。
    【里面调用】_runtime。
    【是否主线】展示帮手。
    """
    rt = _runtime(session_id)
    return rt.status if rt else "idle"


def has_live_approval(session_id: str) -> bool:
    """
    【业务】内存里还有没有「能点下去的允许」。数据库里的 pending_tool 只能画出按钮；
            真正执行要靠 result.interruptions。False 时再点允许会走 STALE_APPROVAL。
    【什么时候用】手机卡/工作台判断这张批准还算不算数。
    【谁调用】duty / sessions 里检查 live 批准时。
    【参数】session_id。
    【里面调用】_runtime。要同时满足：status=approval、还有 result、result 上有 interruptions。
    【是否主线】HITL 帮手。
    """
    rt = _runtime(session_id)
    return bool(rt and rt.status == "approval" and rt.result and getattr(rt.result, "interruptions", None))


def _cancel_result(result: object | None) -> None:
    """
    【业务】告诉 SDK 这一轮马上停，不要再吐字、不要再调工具。
    【什么时候用】你点了工作台「停止」，cancel_run 找到 Runtime 之后。
    【谁调用】cancel_run。
    【参数】result：Runtime 上的 SDK 对象，可能没有 cancel 方法（就忽略）。
    【里面调用】result.cancel("immediate")。
    【是否主线】旁路。热修演示不一定要点停止。
    """
    cancel = getattr(result, "cancel", None)
    if callable(cancel):
        try:
            cancel("immediate")
        except Exception:
            pass


def cancel_run(session_id: str) -> bool:
    """
    【业务】点「停止」：这一轮别再说了。对话还在，收件箱告警也不会关。
    【什么时候用】工作台停止按钮 → chat.cancel_message。
    【谁调用】chat.cancel_message。
    【参数】session_id。返回 True=找到了内存 Runtime 并打上标记；False=这轮已经不在内存里。
    【里面调用】_runtime 取草稿本；_cancel_result 通知 SDK；把 user_cancel 设 True，_produce 走取消分支。
    【是否主线】旁路。
    """
    rt = _runtime(session_id)
    if not rt:
        return False
    log.warning("cancel_run session=%s status=%s", session_id, rt.status)
    rt.user_cancel = True
    _cancel_result(rt.result)
    return True


def _emit(rt: SessionRuntime, ev: dict[str, Any]) -> None:
    """
    【业务】把一件已经发生的事（几个字、工具、批准、结束）编号后，
            既留给刷新回放，又塞给每一个正在听的网页队列。
            某个标签页队列满了（页面大概死了）就摘掉，避免内存一直涨。
    【什么时候用】_produce 每处理完一件事；批准/停止/说完时也会发终端事件。
    【谁调用】_produce。
    【参数】
        rt：这一本 SessionRuntime。
        ev：一件字典事件，进去后会多一个 seq。
    【里面调用】队列 put_nowait。replay 超过 400 条只留最近 240 条。
    【是否主线】是。网页能边生成边出字，靠它推进去。
    """
    rt.seq += 1
    ev = {**ev, "seq": rt.seq}
    rt.replay.append(ev)
    if len(rt.replay) > 400:
        rt.replay = rt.replay[-240:]
    for q in list(rt.subscribers):
        try:
            q.put_nowait(ev)
        except asyncio.QueueFull:
            try:
                rt.subscribers.remove(q)
            except ValueError:
                pass


async def subscribe_events(session_id: str, after: int = 0) -> AsyncIterator[dict[str, Any]]:
    """
    【业务】网页来听这一轮：先把「我还没见过的旧事件」补上（after 是上次看到的序号），
            再实时收新的。约 12 秒没动静发 ping，防止中间代理把连接掐掉。
            听到 done / cancelled / error / approval_needed 就结束这次订阅。
    【什么时候用】send_message、刷新 follow_events、点允许之后的 _relay；评测脚本的封装也会听。
    【谁调用】chat._relay；stream_prompt / resume_after_approval。
    【参数】
        session_id：听哪一次对话。
        after：已经看到的最大 seq，0 表示从当前 replay 里该给的都给。
    【里面调用】
        _runtime：没有草稿本就 yield 一条空 done。
        把新队列挂到 subscribers；先把 replay 里 seq>after 的放进队列。
    【是否主线】是。刷新后续看同一轮，不重发用户那句话。
    """
    rt = _runtime(session_id)
    if rt is None:
        yield {"type": "done", "text": ""}
        return
    q: asyncio.Queue = asyncio.Queue(maxsize=512)
    rt.subscribers.append(q)
    for ev in rt.replay:
        if int(ev.get("seq") or 0) > after:
            await q.put(ev)
    try:
        while True:
            try:
                ev = await asyncio.wait_for(q.get(), timeout=12)
            except (TimeoutError, asyncio.TimeoutError):
                yield {"type": "ping"}
                continue
            yield ev
            if ev.get("type") in TERMINAL:
                return
    finally:
        if q in rt.subscribers:
            rt.subscribers.remove(q)


async def _produce(session_id: str, rt: SessionRuntime, result: RunResultStreaming) -> None:
    """
    【业务】这一轮模型在服务器上的保姆：边收字边推网页，该停批准就停，结束再写库。
            不是每个字重启一次，而是开跑时 create_task 挂上，这一轮停住或说完就结束。
    【什么时候用】start_prompt（新发一句）和 start_resume（点允许/拒绝）里 asyncio.create_task。
    【谁调用】不被路由直接调；由 start_prompt / start_resume 后台启动。
    【参数】
        session_id：哪一次对话。
        rt：这一本草稿，用来 _emit、改 status、留 result。
        result：这一轮 Runner.run_streamed 的流式结果。
    【里面调用】
        iter_stream_events：翻译 SDK 流。
        add_message：工具摘要、最终回答、「已停止」。
        _emit：推给网页。
        close_unanswered_tool_calls：落库前补洞。
        _safe_save：歇脚点写 agent_states（批准时带 pending）。
        result.to_input_list / final_output / interruptions：SDK 历史、终稿、待批准列表。
    【是否主线】是。热修第三环真正出字、弹批准，都在这里。
    """
    factory = get_session_factory()
    collected = ""
    try:
        async for ev in iter_stream_events(result):
            if ev.get("type") == "text_delta":
                collected += ev.get("text") or ""
            if ev.get("type") == "tool_called":
                async with factory() as db:
                    await add_message(
                        db,
                        session_id,
                        "assistant",
                        f"{ev.get('tool')} {ev.get('arguments') or ''}",
                        kind="tool",
                    )
            _emit(rt, ev)
        rt.input_items = close_unanswered_tool_calls(result.to_input_list())
        if result.interruptions and not rt.user_cancel:
            pending_tool = ""
            pending_args = ""
            for item in result.interruptions:
                raw = getattr(item, "raw_item", None)
                pending_tool = getattr(raw, "name", "tool")
                pending_args = getattr(raw, "arguments", "") or ""
                _emit(
                    rt,
                    {
                        "type": "approval_needed",
                        "tool": pending_tool,
                        "arguments": pending_args,
                        "call_id": getattr(raw, "call_id", "") or getattr(raw, "id", ""),
                    },
                )
            rt.status = "approval"
            await _safe_save(session_id, rt.input_items, pending_tool, pending_args)
            return
        if rt.user_cancel:
            rt.status = "cancelled"
            await _safe_save(session_id, rt.input_items)
            async with factory() as db:
                if collected:
                    await add_message(db, session_id, "assistant", collected, commit=False)
                await add_message(db, session_id, "system", "已停止")
            _emit(rt, {"type": "cancelled", "message": "已停止"})
            return
        rt.status = "done"
        await _safe_save(session_id, rt.input_items)
        text = result.final_output or collected
        if text:
            async with factory() as db:
                await add_message(db, session_id, "assistant", text)
        _emit(rt, {"type": "done", "text": text or ""})
    except Exception as exc:
        log.exception("agent run failed")
        if result.interruptions and not rt.user_cancel:
            rt.status = "approval"
            return
        rt.status = "error"
        _emit(rt, {"type": "error", "message": str(exc)})
    finally:
        if rt.result is result and rt.status not in {"approval"}:
            rt.result = None


def _ensure_runtime(session_id: str, items: list[Any] | None = None) -> SessionRuntime:
    """
    【业务】保证这个会话在本进程里有一本 Runtime。没有就新建并放进 runtimes 字典。
    【什么时候用】start_prompt 一进来，准备开跑或复用已有草稿。
    【谁调用】start_prompt。
    【参数】
        session_id：字典的键。
        items：新建时可以带上已有历史；默认空列表（稍后可能 load_input_items）。
    【里面调用】runtimes.get；没有则 SessionRuntime(...) 再写入 runtimes。
    【是否主线】帮手。没有它 start_prompt 没地方挂 result 和 _produce。
    """
    rt = runtimes.get(session_id)
    if rt is None:
        rt = SessionRuntime(input_items=list(items or []))
        runtimes[session_id] = rt
    return rt


async def start_prompt(
    session_id: str,
    workspace: Path,
    mode: str,
    user_text: str,
    model_id: str | None = None,
    actor: str = "local",
    role: str = "",
    auto_approve: bool | None = None,
    allow_git_push: bool = True,
) -> SessionRuntime:
    """
    【业务】把用户这一句交给模型，开始新的一轮工具循环。investigate 不会进这里。
            已经在出字或等批准：直接返回现有 Runtime，让网页去听，不要开第二轮。
    【什么时候用】工作台回车、排查后代发任务说明、快捷按钮、评测脚本发题。
    【谁调用】chat.send_message；评测 drain_run / stream_prompt。
    【参数】
        session_id：哪一次对话。
        workspace：工具根目录 Path。
        mode：plan / build。
        user_text：这一句正文（可能已加「当前编辑器打开」前缀）。
        model_id：会话锁定的模型，可空。
        actor / role：谁在操作。
        auto_approve / allow_git_push：评测或策略开关，值班台默认不自动批。
    【里面调用】
        _ensure_runtime / is_active / load_input_items / close_unanswered_tool_calls：
            准备历史，补残缺 tool call。
        build_agent / make_forge_context：组助手和工具上下文。
        Runner.run_streamed：SDK 开跑，最多 40 轮工具，防止死循环。
        create_task(_produce)：后台收流推网页。
    【是否主线】是。热修第三环从这里模型才开口。
    """
    rt = _ensure_runtime(session_id)
    if is_active(session_id) and rt.status == "running":
        return rt
    if rt.status == "approval":
        return rt
    if rt.input_items == []:
        rt.input_items = await load_input_items(session_id)
    rt.user_cancel = False
    rt.replay = []
    rt.seq = 0
    rt.input_items = close_unanswered_tool_calls(rt.input_items)
    rt.input_items.append({"role": "user", "content": user_text})
    rt.original_intent = user_text
    agent = build_agent(mode, model_id, workspace)
    ctx = make_forge_context(
        session_id,
        workspace,
        mode,
        actor=actor,
        model_id=model_id,
        role=role,
        auto_approve=auto_approve,
        allow_git_push=allow_git_push,
        original_intent=rt.original_intent,
    )
    result = Runner.run_streamed(agent, rt.input_items, context=ctx, max_turns=40)
    rt.result = result
    rt.status = "running"
    rt.task = asyncio.create_task(_produce(session_id, rt, result), name=f"forge-run-{session_id[:8]}")
    return rt


async def start_resume(
    session_id: str,
    workspace: Path,
    mode: str,
    approved: bool,
    always: bool,
    model_id: str | None = None,
    actor: str = "local",
    role: str = "",
    auto_approve: bool | None = None,
    allow_git_push: bool = True,
) -> SessionRuntime:
    """
    【业务】你点了允许或拒绝：不是从头再问一遍，而是接着刚才停住的那次 write_file（等）。
            允许则工具会真的改盘；拒绝则这次不执行，模型收到拒绝再想办法。
            内存里批准对象没了（重启过）：补历史、存库、抛 STALE_APPROVAL，叫你重发上一句。
    【什么时候用】工作台点允许/拒绝；手机钉钉卡后台 resume。
    【谁调用】chat.approve；duty._resume_in_background / resume_after_approval。
    【参数】
        approved：True 允许，False 拒绝。
        always：True 表示本会话后续同类工具先别再问（对应网页 scope=session）。
        其余与 start_prompt 相同：会话、工作区、mode、模型、谁在点。
    【里面调用】
        close_unanswered_tool_calls / load_input_items / save_agent_state：失效时补洞落库。
        result.to_state / approve / reject：SDK 在同一条中断上拍板。
        build_agent / make_forge_context / Runner.run_streamed / _produce：再开一轮。
    【是否主线】是。HITL 点头后热修才能真正改 bugs.py。
    """
    rt = runtimes.get(session_id)
    if rt is None or rt.result is None or not rt.result.interruptions:
        items = close_unanswered_tool_calls(rt.input_items if rt else await load_input_items(session_id))
        if rt:
            rt.input_items = items
            rt.result = None
            rt.status = "idle"
        await save_agent_state(session_id, items)
        raise RuntimeError(STALE_APPROVAL)
    state = rt.result.to_state()
    for interruption in rt.result.interruptions:
        if approved:
            state.approve(interruption, always_approve=always)
        else:
            state.reject(interruption)
    rt.user_cancel = False
    rt.replay = []
    rt.seq = 0
    agent = build_agent(mode, model_id, workspace)
    ctx = make_forge_context(
        session_id,
        workspace,
        mode,
        actor=actor,
        model_id=model_id,
        role=role,
        auto_approve=auto_approve,
        allow_git_push=allow_git_push,
        original_intent=rt.original_intent,
    )
    result = Runner.run_streamed(agent, state, context=ctx, max_turns=40)
    rt.result = result
    rt.status = "running"
    rt.task = asyncio.create_task(_produce(session_id, rt, result), name=f"forge-run-{session_id[:8]}")
    return rt


async def stream_prompt(
    session_id: str,
    workspace: Path,
    mode: str,
    user_text: str,
    model_id: str | None = None,
    actor: str = "local",
) -> AsyncIterator[dict[str, Any]]:
    """
    【业务】评测脚本要「发一句然后把事件收齐」，不必自己写 start + 订阅。丢掉 ping（脚本不需要保活）。
    【什么时候用】自动化评测、drain 一轮对话。工作台不用它，工作台用 send_message + _relay。
    【谁调用】评测脚本。
    【参数】与 start_prompt 前几个相同：会话、工作区、mode、用户原文、模型、actor。
    【里面调用】start_prompt 开跑；subscribe_events 收事件。
    【是否主线】旁路。值班台主线不走这里。
    """
    await start_prompt(session_id, workspace, mode, user_text, model_id, actor)
    async for ev in subscribe_events(session_id):
        if ev.get("type") == "ping":
            continue
        yield ev


async def resume_after_approval(
    session_id: str,
    workspace: Path,
    mode: str,
    approved: bool,
    always: bool,
    model_id: str | None = None,
    actor: str = "local",
) -> AsyncIterator[dict[str, Any]]:
    """
    【业务】评测或 duty 后台任务：点完允许后既要 start_resume，又要听后续事件。丢掉 ping。
    【什么时候用】duty._resume_in_background；评测里模拟批准。工作台用 chat.approve + _relay。
    【谁调用】duty.resume_after_approval 的调用方（卡片后台）；评测脚本。
    【参数】与 start_resume 相同：会话、工作区、mode、approved、always、模型、actor。
    【里面调用】start_resume；subscribe_events。
    【是否主线】HITL 旁路封装。网页主路径拆开写，不经过这个函数。
    """
    await start_resume(session_id, workspace, mode, approved, always, model_id, actor)
    async for ev in subscribe_events(session_id):
        if ev.get("type") == "ping":
            continue
        yield ev
