"""
内置模型菜单：厂商分组 + 是否已配 Key。

【在 Agent 里的位置】
    右上角 ModelMenu 调 GET /api/models，没 Key 的标「未配密钥」。
    真正发请求仍是 agent_runtime.resolve_model。
"""

from __future__ import annotations

from typing import Any

from app.core.config import get_settings

BUILTIN_PROVIDERS: list[dict[str, Any]] = [
    {
        "id": "deepseek",
        "label": "DeepSeek",
        "default": True,
        "variants": [
            {"id": "deepseek/deepseek-chat", "label": "Flash"},
            {"id": "deepseek/deepseek-reasoner", "label": "Pro"},
        ],
    },
    {
        "id": "glm",
        "label": "GLM 智谱",
        "default": False,
        "variants": [
            {"id": "zhipuai/glm-4-flash", "label": "Flash"},
            {"id": "zhipuai/glm-4.5", "label": "4.5"},
            {"id": "zhipuai/glm-4-plus", "label": "Plus"},
        ],
    },
    {
        "id": "qwen",
        "label": "千问",
        "default": False,
        "variants": [
            {"id": "dashscope/qwen-turbo", "label": "Turbo"},
            {"id": "dashscope/qwen-plus", "label": "Plus"},
            {"id": "dashscope/qwen-max", "label": "Max"},
        ],
    },
    {
        "id": "grok",
        "label": "Grok",
        "default": False,
        "variants": [
            {"id": "xai/grok-3", "label": "Grok 3 · xAI"},
            {"id": "xai/grok-4", "label": "Grok 4 · xAI"},
            {"id": "cursor/grok-4", "label": "Grok 4 · Cursor 网关"},
        ],
    },
    {
        "id": "openai",
        "label": "OpenAI",
        "default": False,
        "variants": [
            {"id": "gpt-4.1", "label": "GPT-4.1"},
            {"id": "gpt-4o", "label": "GPT-4o"},
        ],
    },
    {
        "id": "anthropic",
        "label": "Anthropic",
        "default": False,
        "variants": [
            {"id": "anthropic/claude-sonnet-4-5", "label": "Sonnet"},
            {"id": "anthropic/claude-opus-4-1", "label": "Opus"},
        ],
    },
]


def provider_configured(provider_id: str) -> bool:
    """【功能】该厂商在 .env 里有没有 Key。输入 provider id，输出 bool。"""
    s = get_settings()
    return {
        "deepseek": bool(s.deepseek_api_key),
        "glm": bool(s.zhipuai_api_key or s.zai_api_key),
        "qwen": bool(s.dashscope_api_key),
        "grok": bool(s.xai_api_key or (s.cursor_api_key and s.cursor_api_base)),
        "openai": bool(s.openai_api_key),
        "anthropic": bool(s.anthropic_api_key),
    }.get(provider_id, False)


def catalog_with_status() -> list[dict[str, Any]]:
    """【功能】给每家厂商打上 configured。输出给 list_models。"""
    out = []
    for p in BUILTIN_PROVIDERS:
        item = {**p, "configured": provider_configured(p["id"])}
        out.append(item)
    return out


def provider_of(model_id: str) -> str:
    """【功能】自定义模型归到哪个菜单分组。认不出则 custom。"""
    mid = (model_id or "").lower()
    if mid.startswith("deepseek") or "deepseek" in mid:
        return "deepseek"
    if mid.startswith(("zhipu", "zai/", "glm-")):
        return "glm"
    if mid.startswith("dashscope") or mid.startswith("qwen"):
        return "qwen"
    if mid.startswith("xai/") or mid.startswith("cursor/") or mid.startswith("grok"):
        return "grok"
    if mid.startswith("anthropic") or mid.startswith("claude"):
        return "anthropic"
    if mid.startswith("gpt-") or mid.startswith("openai"):
        return "openai"
    return "custom"
