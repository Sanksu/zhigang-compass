"""认证路由：登录、刷新 Token、注册、登出、当前用户。"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.schemas.common import ok, error

router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(BaseModel):
    username: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


@router.post("/login")
async def login(req: LoginRequest):
    """用户登录，返回双 Token。"""
    # TODO: 从 DB 查询用户。当前使用占位逻辑演示。
    # 暂用内置 admin 账户便于开发
    if req.username == "admin" and req.password == "admin123":
        user_id = "00000000-0000-0000-0000-000000000001"
        role = "admin"
        access_token = create_access_token(user_id, role)
        refresh_token = create_refresh_token(user_id)
        return ok(data={
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "expires_in": 1800,
        })

    # 注册用户通过 DB 查询（待 BE-M3-03 集成）
    # async with db session ...
    return error(401, "用户名或密码错误")


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
async def register(req: RegisterRequest):
    """注册新用户。"""
    if len(req.username) < 3 or len(req.password) < 6:
        return error(400, "用户名至少 3 字符，密码至少 6 字符")
    # TODO: 插入 DB（users 表）
    # hashed = hash_password(req.password)
    # async with db session ...
    return ok(data={"msg": "注册成功（待 DB 集成后生效）"})


@router.get("/me")
async def me():
    """获取当前用户信息（需登录）。"""
    # TODO: 对接 Depends(get_current_user) + DB 查询
    return error(501, "待实现 — 对接 Depends + DB")


@router.post("/logout")
async def logout():
    """登出（前端清除 Token，服务端可选黑名单）。"""
    # TODO: 将 refresh_token 加入 Redis 黑名单（TTL = 7d）
    return ok(data={"msg": "已登出"})