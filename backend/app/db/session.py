"""
异步 PostgreSQL 连接。

【在 Agent 里的位置】
    Agent 每次落库（保存 input_items、写消息、写告警）都要拿到一个 AsyncSession。
    FastAPI 路由用依赖注入 `Depends(get_db)`；后台任务（Runtime、watchdog）自己
    `get_session_factory()` 开会话。

【思路】
    进程内懒创建单一引擎，避免每个请求新建连接池。
    URL 来自 .env 的 DATABASE_URL（asyncpg）。

【调用关系】
    被几乎所有 router / service 调用。
    调用 app.core.config.get_settings() 读库地址。
"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine():
    """
    【功能】拿到（或首次创建）全局异步引擎。
    【思路】第一次调用时按 Settings.database_url 建池，之后复用。
    【输入】无（读环境配置）。
    【输出】SQLAlchemy AsyncEngine。
    【调用】get_session_factory()、Alembic 以外的运行时入口。
    """
    global _engine, _session_factory
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(settings.database_url, pool_pre_ping=True)
        _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """
    【功能】给后台任务一个“自己开/关会话”的工厂。
    【思路】Agent Runtime、watchdog、备份不走 FastAPI 依赖，所以不能用 get_db()。
    【输入】无。
    【输出】async_sessionmaker，用法：`async with factory() as db:`。
    【调用】agent_runtime.save_agent_state、watchdog.tick、backup.snapshot_before_change 等。
    """
    if _session_factory is None:
        get_engine()
    assert _session_factory is not None
    return _session_factory


async def get_db() -> AsyncIterator[AsyncSession]:
    """
    【功能】FastAPI 依赖：一个请求一个会话，请求结束自动关闭。
    【思路】yield 出会话，路由里 commit/rollback，离开上下文即释放连接。
    【输入】无（由 FastAPI Depends 注入）。
    【输出】异步生成器，产出 AsyncSession。
    【调用】所有 routers 的 `db: AsyncSession = Depends(get_db)`。
    """
    factory = get_session_factory()
    async with factory() as session:
        yield session
