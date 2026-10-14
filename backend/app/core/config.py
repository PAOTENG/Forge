"""
全局配置：读项目根目录 .env，给 Agent / 沙箱 / 值班通道提供开关和密钥。

【在 Agent 里的位置】
    Runtime 选模型、沙箱限内存、watchdog 是否巡检、热修工作区路径，都从这里读。
    这不是 Agent 框架配置，是值班机本地的一份 Settings。

【思路】
    pydantic-settings 把环境变量映射成字段；缺省值保证没配 Key 时进程也能起来
    （健康检查能返回“未配密钥”，真正跑 Agent 时 resolve_model 再报错）。

【调用关系】
    几乎所有 service 都会 get_settings()。
    路径常量 REPO_ROOT / BACKEND_ROOT 给备份目录、插件库、Alembic 定位用。
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py -> 向上三级到仓库根
REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """
    【功能】一份 .env 的类型化视图。
    【思路】extra=ignore，未知环境变量不炸；密钥默认空字符串。
    【输入】项目根 .env + 进程环境变量。
    【输出】各字段见下方注释。
    """

    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- 模型密钥：Runtime.resolve_model 按优先级选用 ----
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    deepseek_api_key: str = ""
    zhipuai_api_key: str = ""
    zai_api_key: str = ""
    dashscope_api_key: str = ""
    dashscope_api_base: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    xai_api_key: str = ""
    cursor_api_key: str = ""
    cursor_api_base: str = ""
    forge_model: str = ""  # 用户还没在菜单里切过模型时的兜底

    # ---- 鉴权：机器 Token 可当管理员；告警 webhook 另用 duty token ----
    forge_api_token: str = ""
    database_url: str = "postgresql+asyncpg://forge:forge@127.0.0.1:5432/forge"

    # ---- Docker 沙箱（对应 .env 的 FORGE_SANDBOX_*）----
    # 业务：模型要跑 bash / 普通仓 pytest 时进容器，不在你登录的 shell 里执行。
    # 谁读：sandbox.SandboxManager.ensure → containers.run(image/mem/network)。
    # 改完须重启 uvicorn。pids=256 不在这里，写死在 sandbox.py。
    forge_sandbox_image: str = "forge-runner:latest"  # 没有就现编 infra/sandbox/Dockerfile
    forge_sandbox_memory: str = "2g"  # 容器内存上限，防 fork 炸弹占满值班机
    forge_sandbox_network: str = "none"  # none=没网，容器里 pip/curl 外网会失败
    forge_api_host: str = "127.0.0.1"
    forge_api_port: int = 8787
    forge_public_url: str = "http://127.0.0.1:5173"

    # ---- 值班通道：企微 / 钉钉只负责把人点进网页 ----
    forge_duty_token: str = ""
    forge_duty_workspace: str = ""
    forge_duty_notify_user: str = "@all"
    wecom_corp_id: str = ""
    wecom_corp_secret: str = ""
    wecom_agent_id: str = ""
    dingtalk_webhook: str = ""
    dingtalk_secret: str = ""
    duty_webhook_url: str = ""

    # ---- 被监控业务 order-api：supervisor + watchdog 用 ----
    forge_target_name: str = "order-api"
    forge_target_cwd: str = ""
    forge_target_cmd: str = ""
    forge_target_health: str = "http://127.0.0.1:8790/health"
    forge_target_canary: str = "http://127.0.0.1:8790/canary"
    forge_target_autostart: bool = True
    forge_watchdog: bool = True
    forge_watchdog_cooldown: int = 120
    # True 时管理员调变更工具跳过 HITL；无头评测另设 ForgeContext.auto_approve
    forge_admin_auto_approve: bool = False

    # RoomCheck 业务 Agent（A2A 对等体，不是 order-api）
    roomcheck_a2a_url: str = "http://127.0.0.1:8791"
    # Ledger 知识库问答 Agent，与本仓库同级的 ledger-kb
    kb_a2a_url: str = "http://127.0.0.1:8793"
    a2a_token: str = "forge-roomcheck-a2a"
    a2a_jwt_secret: str = ""  # 空则用 a2a_token 签短时 JWT
    a2a_jwt_ttl_seconds: int = 300
    # 对端尚未切 JWT 时允许旧的静态 Bearer；生产应收成 false
    a2a_allow_legacy_token: bool = True
    a2a_pin_cards: bool = True
    a2a_tls_ca: str = ""
    a2a_tls_cert: str = ""
    a2a_tls_key: str = ""
    forge_audit_fail_closed: bool = True

    @property
    def sync_database_url(self) -> str:
        """
        【功能】把 asyncpg URL 改成同步驱动，给 Alembic 迁移用。
        【输入】self.database_url。
        【输出】postgresql://... 字符串。
        """
        return self.database_url.replace("postgresql+asyncpg", "postgresql")


@lru_cache
def get_settings() -> Settings:
    """
    【功能】进程内单例读取配置。改 .env 后必须重启 API。
    【思路】lru_cache 避免每个请求重新解析文件。
    【输入】无。输出：Settings。
    【调用】agent_runtime、sandbox.ensure（读沙箱三项）、watchdog、security、duty 等。
    """
    return Settings()
