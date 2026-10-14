"""SDK 工具 Guardrail：执行前拦密钥/外发/意图漂移，执行后脱敏回传。"""

from __future__ import annotations

import json
import logging
import re

from agents import (
    ToolGuardrailFunctionOutput,
    ToolInputGuardrailData,
    ToolOutputGuardrailData,
    tool_input_guardrail,
    tool_output_guardrail,
)

from app.services.dlp import EGRESS_RE, regex_scan, redact_text, scan_text

log = logging.getLogger("forge.guardrail")

_HOTFIX_HINT = re.compile(r"order-api|bugs\.py|热修|金丝雀|canary", re.I)
_ROOM_HINT = re.compile(r"roomcheck|客房|验收|茶具|房号", re.I)


def _args_text(data: ToolInputGuardrailData) -> str:
    return data.context.tool_arguments or ""


def _tool_name(data: ToolInputGuardrailData) -> str:
    return (data.context.tool_name or "").strip()


def _original_intent(data: ToolInputGuardrailData) -> str:
    forge = getattr(data.context, "context", None)
    return (getattr(forge, "original_intent", "") or "").strip()


@tool_input_guardrail
def block_secrets_in_args(data: ToolInputGuardrailData) -> ToolGuardrailFunctionOutput:
    result = scan_text(_args_text(data))
    if result.should_block:
        return ToolGuardrailFunctionOutput.reject_content(
            f"拒绝：工具参数含敏感数据（{'、'.join(result.blocked)}）。已阻断并记审计。",
            output_info={"hits": result.blocked},
        )
    return ToolGuardrailFunctionOutput.allow(output_info={"hits": result.hits})


@tool_input_guardrail
def block_egress_in_args(data: ToolInputGuardrailData) -> ToolGuardrailFunctionOutput:
    name = _tool_name(data)
    raw = _args_text(data)
    if name in {"bash", "run_tests"} and EGRESS_RE.search(raw):
        return ToolGuardrailFunctionOutput.reject_content(
            "拒绝：命令含 curl/wget/scp/ssh 等外发。已阻断。",
            output_info={"egress": True},
        )
    return ToolGuardrailFunctionOutput.allow()


@tool_input_guardrail
def block_intent_drift(data: ToolInputGuardrailData) -> ToolGuardrailFunctionOutput:
    """只看用户原话 + 本次工具名/参数，不看中间不可信上下文。"""
    intent = _original_intent(data)
    if not intent:
        return ToolGuardrailFunctionOutput.allow()
    name = _tool_name(data)
    args = _args_text(data).lower()
    if name == "a2a_roomcheck" and _HOTFIX_HINT.search(intent) and not _ROOM_HINT.search(intent):
        if "inspect" in args and "canary" not in args:
            return ToolGuardrailFunctionOutput.reject_content(
                "拒绝：当前任务是软件热修，不能把客房验收派给业务 Agent。",
                output_info={"drift": "a2a_inspect"},
            )
    return ToolGuardrailFunctionOutput.allow()


@tool_output_guardrail
def redact_tool_output(data: ToolOutputGuardrailData) -> ToolGuardrailFunctionOutput:
    raw = data.output
    text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False, default=str)
    redacted = redact_text(text)
    if redacted != text:
        log.info("tool output redacted tool=%s", _tool_name(data))
        return ToolGuardrailFunctionOutput.reject_content(
            redacted,
            output_info={"redacted": True, "regex": regex_scan(text).hits},
        )
    return ToolGuardrailFunctionOutput.allow()


MUTATING_INPUT_GUARDRAILS = [
    block_secrets_in_args,
    block_egress_in_args,
    block_intent_drift,
]
OUTPUT_GUARDRAILS = [redact_tool_output]
MUTATING_TOOLS = {
    "write_file",
    "delete_file",
    "bash",
    "git_checkout",
    "git_commit",
    "git_push",
    "run_tests",
    "restart_service",
    "a2a_roomcheck",
    "a2a_kb",
}


def attach_tool_guardrails(tools: list) -> None:
    for tool in tools:
        name = getattr(tool, "name", "") or ""
        if hasattr(tool, "tool_output_guardrails"):
            existing = list(tool.tool_output_guardrails or [])
            for item in OUTPUT_GUARDRAILS:
                if item not in existing:
                    existing.append(item)
            tool.tool_output_guardrails = existing
        if name in MUTATING_TOOLS and hasattr(tool, "tool_input_guardrails"):
            existing = list(tool.tool_input_guardrails or [])
            for item in MUTATING_INPUT_GUARDRAILS:
                if item not in existing:
                    existing.append(item)
            tool.tool_input_guardrails = existing
