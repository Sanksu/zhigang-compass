"""FastAPI 依赖注入：JWT 认证 + RBAC 权限检查。"""

from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.errors import ERR_TOKEN_EXPIRED, business_error
from app.core.security import TokenExpiredError, decode_token, has_permission

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
    try:
        payload = decode_token(credentials.credentials)
    except TokenExpiredError:
        # 契约 4011：过期 token 提示刷新（main.py 对 business_error 的 detail 透传）
        raise business_error(ERR_TOKEN_EXPIRED, "Token 已过期，请刷新") from None
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
    """可选鉴权：匿名/guest 返回 None，有效 token 返回 payload。

    供「匿名可读但登录用户获得更多数据」的接口使用（如图谱按角色
    过滤 candidate 岗位）。无 token 或 token 无效均视为匿名，不抛 401。
    """
    if credentials is None:
        return None
    try:
        payload = decode_token(credentials.credentials)
    except Exception:
        return None
    if payload is None or payload.get("type") != "access":
        return None
    return payload


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


# 角色等级（设计文档 §2.4.3/2.4.4 中 "user+" 语义：guest < user < admin）
_ROLE_RANK = {"guest": 0, "user": 1, "admin": 2}


def require_role(min_role: str = "user"):
    """角色门槛依赖：登录用户角色须达到 min_role 及以上。

    用于设计文档声明 "user+" 的端点（如简历上传/匹配计算），
    guest 角色即使已登录也无权调用。

    用法：
        @router.post("/parse")
        async def parse(user: dict = Depends(require_role("user"))):
            ...
    """

    async def _check(user: dict = Depends(get_current_user)) -> dict:
        if _ROLE_RANK.get(user.get("role", "guest"), 0) < _ROLE_RANK.get(min_role, 1):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"需要 {min_role} 及以上角色",
            )
        return user

    return _check