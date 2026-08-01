"""认证路由：登录、刷新 Token、注册、登出、当前用户。"""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models.business import User
from app.schemas.business import LoginRequest, RefreshRequest, RegisterRequest
from app.schemas.common import ok, error

router = APIRouter()


@router.post("/login")
async def login(req: LoginRequest, db: AsyncSession = Depends(get_db)):
    """用户登录，返回双 Token（凭据校验走 users 表）。"""
    user = await db.scalar(select(User).where(User.username == req.username))
    if user is None:
        # 首次部署 bootstrap：users 表为空时按配置创建 admin 用户，
        # 创建后即落入 users 表，后续登录走 DB 校验
        if req.username == settings.admin_username and req.password == settings.admin_password:
            user = User(
                username=req.username,
                password_hash=hash_password(req.password),
                role="admin",
            )
            db.add(user)
            await db.commit()
            await db.refresh(user)
        else:
            return error(401, "用户名或密码错误")
    elif not verify_password(req.password, user.password_hash):
        return error(401, "用户名或密码错误")

    if not user.is_active:
        return error(403, "账户已禁用")

    access_token = create_access_token(user.id, user.role)
    refresh_token = create_refresh_token(user.id, user.role)
    return ok(data={
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in": 1800,
    })


@router.post("/refresh")
async def refresh_token(req: RefreshRequest):
    """刷新 access_token。"""
    payload = decode_token(req.refresh_token)
    if payload is None or payload.get("type") != "refresh":
        return error(401, "无效的 refresh_token")
    user_id = payload["sub"]
    # 重新签发（短 TTL 的 access_token）
    new_access = create_access_token(user_id, payload.get("role", "guest"))
    return ok(data={"access_token": new_access, "expires_in": 1800})


@router.post("/register")
async def register(req: RegisterRequest, db: AsyncSession = Depends(get_db)):
    """注册新用户（默认 guest 角色）。"""
    if len(req.username) < 3 or len(req.password) < 6:
        return error(400, "用户名至少 3 字符，密码至少 6 字符")

    existing = await db.scalar(select(User).where(User.username == req.username))
    if existing is not None:
        return error(409, "用户名已存在")

    user = User(
        username=req.username,
        password_hash=hash_password(req.password),
        role="guest",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return ok(data={"id": user.id, "username": user.username, "role": user.role})


@router.get("/me")
async def me(
    payload: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取当前用户信息（需登录）。"""
    user = await db.get(User, payload["sub"])
    if user is None or not user.is_active:
        return error(404, "用户不存在")
    return ok(data={"id": user.id, "username": user.username, "role": user.role})


@router.post("/logout")
async def logout():
    """登出（前端清除 Token，服务端可选黑名单）。"""
    # TODO: 将 refresh_token 加入 Redis 黑名单（TTL = 7d）
    return ok(data={"msg": "已登出"})
