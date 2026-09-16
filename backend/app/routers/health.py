"""
健康检查：模型是否已选、各厂商 Key、值班通道是否配置。前端启动页会打这个。
"""

from fastapi import APIRouter

from app.core.config import get_settings
from app.services.agent_runtime import model_label

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    """【功能】GET /api/health。输出 ok、当前模型、各 Key/通道布尔值。"""
    settings = get_settings()
    return {
        "ok": True,
        "model": model_label(),
        "has_openai": bool(settings.openai_api_key),
        "has_anthropic": bool(settings.anthropic_api_key),
        "has_deepseek": bool(settings.deepseek_api_key),
        "has_zhipu": bool(settings.zhipuai_api_key or settings.zai_api_key),
        "has_dashscope": bool(settings.dashscope_api_key),
        "has_xai": bool(settings.xai_api_key),
        "has_cursor": bool(settings.cursor_api_key and settings.cursor_api_base),
        "duty": {
            "wecom": bool(settings.wecom_corp_id and settings.wecom_corp_secret and settings.wecom_agent_id),
            "dingtalk": bool(settings.dingtalk_webhook),
            "webhook": bool(settings.duty_webhook_url),
            "workspace": True,
            "public_url": settings.forge_public_url.rstrip("/"),
        },
    }
