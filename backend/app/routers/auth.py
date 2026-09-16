from __future__ import annotations

from datetime import datetime, timezone

"""
首次启用、登录登出、用户角色管理。

【在 Agent 里的位置】
    viewer 不能发消息/批准。机器 Token 不走登录表。
"""

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import (
    AUTH_COOKIE,
    CurrentUser,
    hash_password,
    new_session_token,
    require_admin,
    require_user,
    user_count,
    verify_password,
)
from app.db.session import get_db
from app.models.user import AuthSessionRow, UserRow

router = APIRouter(prefix="/auth", tags=["auth"])


class SetupIn(BaseModel):
    username: str = Field(min_length=2, max_length=40)
    password: str = Field(min_length=8, max_length=128)
    display_name: str = ""


class LoginIn(BaseModel):
    username: str
    password: str
    remember: bool = False


class UserIn(BaseModel):
    username: str = Field(min_length=2, max_length=40)
    password: str = Field(min_length=8, max_length=128)
    role: str = "engineer"
    display_name: str = ""


class RoleIn(BaseModel):
    role: str


def _public(user: UserRow) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "display_name": user.display_name or user.username,
    }


def _auth_cookie(response: Response, token: str, max_age: int | None) -> None:
    response.set_cookie(
        AUTH_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        path="/",
        max_age=max_age,
        secure=False,
    )


def _auth_payload(user: UserRow, token: str, exp: datetime, remember: bool) -> dict:
    return {
        "ok": True,
        "token": token,
        "user": _public(user),
        "expires_at": exp.astimezone(timezone.utc).isoformat(),
        "remember": remember,
    }


@router.get("/status")
async def auth_status(db: AsyncSession = Depends(get_db)) -> dict:
    n = await user_count(db)
    return {"ok": True, "needs_setup": n == 0, "users": n}


@router.post("/setup")
async def setup(body: SetupIn, db: AsyncSession = Depends(get_db)) -> JSONResponse:
    if await user_count(db) > 0:
        raise HTTPException(400, "管理员已创建，请登录")
    name = body.username.strip().lower()
    if not name.isalnum():
        raise HTTPException(400, "用户名只能是字母和数字")
    user = UserRow(
        username=name,
        password_hash=hash_password(body.password),
        role="admin",
        display_name=body.display_name.strip() or name,
    )
    db.add(user)
    await db.flush()
    token, exp = new_session_token(days=3)
    db.add(AuthSessionRow(token=token, user_id=user.id, expires_at=exp))
    await db.commit()
    resp = JSONResponse(_auth_payload(user, token, exp, True))
    _auth_cookie(resp, token, 3 * 24 * 3600)
    return resp


@router.post("/login")
async def login(body: LoginIn, db: AsyncSession = Depends(get_db)) -> JSONResponse:
    name = body.username.strip().lower()
    user = (await db.execute(select(UserRow).where(UserRow.username == name))).scalar_one_or_none()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "用户名或密码错误")
    days = 3 if body.remember else 0.5
    token, exp = new_session_token(days=days)
    db.add(AuthSessionRow(token=token, user_id=user.id, expires_at=exp))
    await db.commit()
    resp = JSONResponse(_auth_payload(user, token, exp, body.remember))
    _auth_cookie(resp, token, 3 * 24 * 3600 if body.remember else None)
    return resp


@router.post("/logout")
async def logout(
    db: AsyncSession = Depends(get_db),
    authorization: str | None = Header(default=None),
    _user: CurrentUser = Depends(require_user),
) -> JSONResponse:
    token = authorization[7:].strip() if authorization and authorization.lower().startswith("bearer ") else ""
    if token:
        row = await db.get(AuthSessionRow, token)
        if row:
            await db.delete(row)
            await db.commit()
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(AUTH_COOKIE, path="/")
    return resp


@router.get("/me")
async def me(user: CurrentUser = Depends(require_user)) -> dict:
    return {
        "ok": True,
        "user": {
            "id": user.id,
            "username": user.username,
            "role": user.role,
            "display_name": user.display_name,
        },
    }


@router.get("/users")
async def list_users(
    db: AsyncSession = Depends(get_db), _admin: CurrentUser = Depends(require_admin)
) -> dict:
    rows = (await db.execute(select(UserRow).order_by(UserRow.created_at.asc()))).scalars()
    return {"users": [_public(r) for r in rows]}


@router.post("/users")
async def create_user(
    body: UserIn, db: AsyncSession = Depends(get_db), _admin: CurrentUser = Depends(require_admin)
) -> dict:
    name = body.username.strip().lower()
    if not name.isalnum():
        raise HTTPException(400, "用户名只能是字母和数字")
    if body.role not in {"admin", "engineer", "viewer"}:
        raise HTTPException(400, "角色无效")
    exists = (await db.execute(select(UserRow).where(UserRow.username == name))).scalar_one_or_none()
    if exists:
        raise HTTPException(409, "用户名已存在")
    user = UserRow(
        username=name,
        password_hash=hash_password(body.password),
        role=body.role,
        display_name=body.display_name.strip() or name,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return {"ok": True, "user": _public(user)}


@router.patch("/users/{user_id}")
async def update_user(
    user_id: str,
    body: RoleIn,
    db: AsyncSession = Depends(get_db),
    admin: CurrentUser = Depends(require_admin),
) -> dict:
    if body.role not in {"admin", "engineer", "viewer"}:
        raise HTTPException(400, "角色无效")
    user = await db.get(UserRow, user_id)
    if not user:
        raise HTTPException(404, "用户不存在")
    if user.id == admin.id and body.role != "admin":
        raise HTTPException(400, "不能取消自己的管理员")
    user.role = body.role
    await db.commit()
    return {"ok": True, "user": _public(user)}
