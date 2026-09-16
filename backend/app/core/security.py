"""
登录态与角色闸门。

【在 Agent 里的位置】
    值班台要登录才能开会话、发消息、点批准。viewer 只能看，不能排查。
    机器 Token（FORGE_API_TOKEN）仍可当管理员，方便脚本和无头评测打 API。

【思路】
    密码用 PBKDF2 存哈希；会话 token 进 auth_sessions 表，也接受 Cookie / Bearer / X-Forge-Token。
    路由用 FastAPI Depends：require_user → require_engineer / require_admin。

【调用关系】
    被 auth / chat / sessions / duty / models 等路由依赖。
    读 users、auth_sessions 两张表。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import Cookie, Depends, Header, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.user import AuthSessionRow, UserRow

ROLES = ("admin", "engineer", "viewer")
WRITE_ROLES = {"admin", "engineer"}


def hash_password(password: str) -> str:
    """
    【功能】注册/改密时把明文变成不可逆哈希。
    【思路】随机 salt + PBKDF2-HMAC-SHA256，210000 轮，格式 algo$rounds$salt$hex。
    【输入】password：用户明文密码。
    【输出】可写入 users.password_hash 的字符串。
    【调用】routers/auth.py 的 setup / create_user。
    """
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 210_000)
    return f"pbkdf2_sha256$210000${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """
    【功能】登录时核对密码。
    【思路】拆存储格式，用同一 salt/轮数再算一遍，hmac.compare_digest 防时序泄漏。
    【输入】password 明文；stored 库里的哈希串。
    【输出】True 匹配，False 格式坏或密码错。
    【调用】routers/auth.py login。
    """
    try:
        algo, rounds, salt, expected = stored.split("$", 3)
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(rounds))
    return hmac.compare_digest(digest.hex(), expected)


@dataclass
class CurrentUser:
    """
    【功能】请求期内的登录用户快照，不持有 ORM 对象。
    【字段】id / username / role / display_name。
    【方法】can_write：工程师或管理员；is_admin：仅管理员。
    """

    id: str
    username: str
    role: str
    display_name: str = ""

    def can_write(self) -> bool:
        """工程师/管理员才能开会话、发消息、批准。viewer 返回 False。"""
        return self.role in WRITE_ROLES

    def is_admin(self) -> bool:
        """是否管理员（可管用户、可配 forge_admin_auto_approve）。"""
        return self.role == "admin"


def require_write(user: CurrentUser) -> CurrentUser:
    """
    【功能】同步检查写权限，失败抛 403。
    【输入】已解析的 CurrentUser。
    【输出】原 user；无写权限则 HTTP 403。
    【调用】require_engineer、sessions.create_session。
    """
    if not user.can_write():
        raise HTTPException(403, "只读账号不能执行此操作")
    return user


def require_admin_role(user: CurrentUser) -> CurrentUser:
    """
    【功能】同步检查管理员，失败抛 403。
    【输入】CurrentUser。输出：原 user 或 403。
    【调用】require_admin、用户管理接口。
    """
    if not user.is_admin():
        raise HTTPException(403, "需要管理员")
    return user


def _bearer(authorization: str | None) -> str:
    """
    【功能】从 Authorization: Bearer xxx 抽出 token。
    【输入】原始 Header 或 None。输出：token 或空串。
    """
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return ""


async def user_count(db: AsyncSession) -> int:
    """
    【功能】库里有几个账号。0 表示还没做首次启用，前端应走 setup。
    【输入】db 会话。输出：整数人数。
    【调用】require_user、auth.setup。
    """
    return int((await db.execute(select(func.count()).select_from(UserRow))).scalar() or 0)


AUTH_COOKIE = "forge_auth"


async def require_user(
    db: AsyncSession = Depends(get_db),
    x_forge_token: str | None = Header(default=None, alias="X-Forge-Token"),
    authorization: str | None = Header(default=None),
    forge_auth: str | None = Cookie(default=None, alias=AUTH_COOKIE),
) -> CurrentUser:
    """
    【功能】解析当前请求是谁。这是绝大多数 API 的第一道门。
    【思路】
        1) Cookie / Bearer / X-Forge-Token 任一有值，先查 auth_sessions；
        2) 再比对 FORGE_API_TOKEN，命中则视为管理员 api-token；
        3) 库里没用户 → 401 setup_required；否则 401 login_required。
    【输入】由 FastAPI 注入的 db、三个可选凭证。
    【输出】CurrentUser；失败抛 401。
    【调用】所有 require_token / require_engineer / require_admin 的上游。
    """
    from app.core.config import get_settings

    token = _bearer(authorization) or (x_forge_token or "").strip() or (forge_auth or "").strip()
    if token:
        row = await db.get(AuthSessionRow, token)
        now = datetime.now(timezone.utc)
        if row and row.expires_at > now:
            user = await db.get(UserRow, row.user_id)
            if user:
                return CurrentUser(
                    id=user.id,
                    username=user.username,
                    role=user.role,
                    display_name=user.display_name or user.username,
                )
        expected = get_settings().forge_api_token.strip()
        if expected and hmac.compare_digest(token, expected):
            return CurrentUser(id="token", username="api-token", role="admin", display_name="API")

    if await user_count(db) == 0:
        raise HTTPException(401, detail={"code": "setup_required", "message": "请先创建管理员账号"})
    raise HTTPException(401, detail={"code": "login_required", "message": "请先登录"})


async def require_token(
    user: CurrentUser = Depends(require_user),
) -> CurrentUser:
    """
    【功能】“已登录即可”的别名，只读接口也用它。
    【输入】require_user 的结果。输出：同一 CurrentUser。
    """
    return user


async def require_engineer(
    user: CurrentUser = Depends(require_user),
) -> CurrentUser:
    """
    【功能】发消息、批准、开会话必须是工程师/管理员。
    【输入】CurrentUser。输出：校验后的 user，viewer 会 403。
    【调用】chat.send_message、chat.approve、duty.investigate。
    """
    return require_write(user)


async def require_admin(
    user: CurrentUser = Depends(require_user),
) -> CurrentUser:
    """
    【功能】用户管理等管理接口。
    【输入】CurrentUser。输出：管理员，否则 403。
    """
    return require_admin_role(user)


def new_session_token(*, days: float = 3) -> tuple[str, datetime]:
    """
    【功能】登录成功后签发 cookie 用的随机 token 和过期时间。
    【输入】days：有效天数，默认 3。
    【输出】(hex token, UTC 过期时刻)。
    【调用】routers/auth.py login / setup。
    """
    return secrets.token_hex(32), datetime.now(timezone.utc) + timedelta(days=days)
