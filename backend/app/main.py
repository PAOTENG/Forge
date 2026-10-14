"""
FastAPI 进程入口：挂上所有路由，启动时迁移库、拉起业务、开 watchdog。

【在 Agent 里的位置】
    uvicorn app.main:app。Agent 跑在这个进程里，不在浏览器。
    CORS 允许局域网 Vite，方便手机打开值班台。
"""

import os
from contextlib import asynccontextmanager

# 模型请求直连。系统代理指向 127.0.0.1:7892 但代理未启动时，LiteLLM 会连接被拒绝。
os.environ["DISABLE_AIOHTTP_TRUST_ENV"] = "true"

import litellm
from alembic import command

litellm.disable_aiohttp_trust_env = True
from alembic.config import Config
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.a2a.server import mount_a2a
from app.core.config import BACKEND_ROOT
from app.routers import auth, chat, duty, health, models, plugins, pty, sessions, system


def run_migrations() -> None:
    """【功能】lifespan 里 alembic upgrade head，保证表结构跟上模型。"""
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    command.upgrade(cfg, "head")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """
    【功能】启动：迁移 → 可选 start_target → start_watchdog。关闭：stop_watchdog。
    【思路】拉起业务失败只 pass，避免 order-api 坏了整个 API 起不来。
    """
    run_migrations()
    from app.core.config import get_settings
    from app.services.supervisor import start_target
    from app.services.watchdog import start_watchdog, stop_watchdog

    if get_settings().forge_target_autostart:
        try:
            start_target()
        except Exception:
            pass
    start_watchdog()
    from app.a2a.roomcheck_watchdog import start_roomcheck_watchdog, stop_roomcheck_watchdog

    start_roomcheck_watchdog()
    from app.a2a.kb_watchdog import start_kb_watchdog, stop_kb_watchdog

    start_kb_watchdog()
    yield
    await stop_kb_watchdog()
    await stop_roomcheck_watchdog()
    await stop_watchdog()


app = FastAPI(title="Forge", version="0.6.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_origin_regex=(
        r"https?://(localhost|127\.0\.0\.1|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+"
        r"|172\.(1[6-9]|2\d|3[0-1])\.\d+\.\d+)(:\d+)?"
    ),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(health.router, prefix="/api")
app.include_router(auth.router, prefix="/api")
app.include_router(sessions.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(system.router, prefix="/api")
app.include_router(models.router, prefix="/api")
app.include_router(plugins.router, prefix="/api")
app.include_router(pty.router, prefix="/api")
app.include_router(duty.router, prefix="/api")
mount_a2a(app)
