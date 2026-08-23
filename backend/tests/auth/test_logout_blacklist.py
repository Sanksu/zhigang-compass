"""登出黑名单测试（08-14 低优批次：access token jti 拉黑闭环）。

覆盖：
- logout 将 Authorization 头中的 access token jti 写入 token:blacklist:{jti}
- 过期/非法 access token 不写黑名单（登出幂等）
- get_current_user 对黑名单内 jti 返回 401（code=4010）
- get_optional_user 对黑名单内 jti 按匿名处理（返回 None）

RSA 密钥：fixture 收敛于 tests/conftest.py（临时密钥对注入 settings）。
"""

import asyncio
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from starlette.requests import Request
from starlette.responses import Response

from app.api.deps import get_current_user, get_optional_user
from app.api.v1.auth import logout
from app.core.config import settings
from app.core.security import create_access_token, decode_token


def _make_request(auth_header: str | None) -> Request:
    headers = []
    if auth_header:
        headers.append((b"authorization", auth_header.encode()))
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/auth/logout",
        "headers": headers,
        "query_string": b"",
        "server": ("testserver", 80),
        "client": ("1.2.3.4", 1234),
        "scheme": "http",
    }
    return Request(scope)


class _CaptureRedis:
    """捕获 set 的黑名单桩（get 返回 None = 未拉黑）。"""

    def __init__(self):
        self.sets: list[tuple] = []

    async def get(self, key):
        return None

    async def set(self, name, value, ex=None):
        self.sets.append((name, value, ex))


def test_logout_blacklists_access_jti():
    """登出携带有效 access token → 其 jti 写入黑名单（TTL = access 有效期）。"""
    token = create_access_token("u1", "user")
    jti = decode_token(token)["jti"]
    redis = _CaptureRedis()
    asyncio.run(logout(None, _make_request(f"Bearer {token}"), Response(), redis))
    assert (f"token:blacklist:{jti}", "1") in [
        (name, value) for name, value, _ in redis.sets
    ]


def test_logout_expired_access_token_skips_blacklist():
    """过期 access token 登出：无需拉黑，登出仍幂等成功。"""
    with patch.object(settings, "jwt_access_token_expire_minutes", -1):
        token = create_access_token("u1", "user")
    redis = _CaptureRedis()
    asyncio.run(logout(None, _make_request(f"Bearer {token}"), Response(), redis))
    assert not any(name.startswith("token:blacklist:") for name, _, _ in redis.sets)


def test_logout_invalid_access_token_skips_blacklist():
    """非法 access token（非 JWT）登出：不写黑名单，不抛异常。"""
    redis = _CaptureRedis()
    asyncio.run(logout(None, _make_request("Bearer garbage.token.here"), Response(), redis))
    assert redis.sets == []


def test_get_current_user_blacklisted_jti_emits_4010():
    """黑名单命中的 access token → 401，code=4010。"""
    token = create_access_token("u1", "user")
    jti = decode_token(token)["jti"]
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    class _BlockedRedis:
        async def get(self, key):
            return "1" if key == f"token:blacklist:{jti}" else None

    with pytest.raises(HTTPException) as exc:
        asyncio.run(get_current_user(creds, _BlockedRedis()))
    assert exc.value.status_code == 401
    assert exc.value.detail["code"] == 4010


def test_get_optional_user_blacklisted_jti_returns_none():
    """黑名单命中 → 可选鉴权按匿名处理（None），不抛 401。"""
    token = create_access_token("u1", "user")
    jti = decode_token(token)["jti"]
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    class _BlockedRedis:
        async def get(self, key):
            return "1" if key == f"token:blacklist:{jti}" else None

    assert asyncio.run(get_optional_user(creds, _BlockedRedis())) is None
