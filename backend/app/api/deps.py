"""FastAPI 依赖注入：JWT 认证 + RBAC 权限检查。"""

from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.security import decode_token, has_permission

security = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> dict:
    """解析 JWT 并返回 payload。"""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少认证凭证",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = decode_token(credentials.credentials)
    if payload is None or payload.get("type") != "access":
        # 仅接受 access token：refresh token（7 天有效）不得直通受保护接口
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效或过期的 Token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return payload


async def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Optional[dict]:
    """可选认证：有 Token 则解析，无 Token 返回 None。"""
    if credentials is None:
        return None
    return decode_token(credentials.credentials)


def require_permission(permission: str):
    """RBAC 权限检查依赖。

    用法：
        @router.get("/panorama")
        async def panorama(user: dict = Depends(require_permission("graph:read"))):
            ...
    """

    async def _check(user: dict = Depends(get_current_user)) -> dict:
        if not has_permission(user.get("role", "guest"), permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"权限不足，需要：{permission}",
            )
        return user

    return _check