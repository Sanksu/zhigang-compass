"""认证路由：登录、刷新 Token、注册。"""

from fastapi import APIRouter, Depends, HTTPException

from app.core.security import create_access_token, create_refresh_token, hash_password, verify_password
from app.schemas.common import ok, error

router = APIRouter()


@router.post("/login")
async def login():
    """占位 — 对接 user 模型后实现。"""
    # TODO: 从 DB 查用户 → verify_password → 签发双 Token
    return error(501, "待实现")


@router.post("/refresh")
async def refresh_token():
    return error(501, "待实现")


@router.post("/register")
async def register():
    return error(501, "待实现")
