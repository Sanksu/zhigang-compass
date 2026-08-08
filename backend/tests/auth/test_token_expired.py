"""Token 过期语义测试（契约错误码 4011，设计文档 §2.4.7）。

覆盖：
- decode_token 对过期 token 抛 TokenExpiredError（区别于无效 → None）
- get_current_user 对过期 access_token 抛 HTTPException 401，detail 携带 code=4011
- 正常 token 不受影响（仍返回 payload）
"""

import asyncio
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.security import TokenExpiredError, create_access_token, decode_token


def _expired_access_token(user_id: str = "u1", role: str = "user") -> str:
    """生成已过期 access_token（负有效期 → exp 在过去）。"""
    with patch.object(settings, "jwt_access_token_expire_minutes", -1):
        return create_access_token(user_id, role)


def test_decode_token_expired_raises_token_expired():
    """过期 token → TokenExpiredError；无效 token → None（两者语义区分）。"""
    with pytest.raises(TokenExpiredError):
        decode_token(_expired_access_token())
    assert decode_token("garbage.token.here") is None


def test_decode_token_valid_returns_payload():
    token = create_access_token("u1", "user")
    payload = decode_token(token)
    assert payload["sub"] == "u1"
    assert payload["type"] == "access"


def test_get_current_user_expired_token_emits_4011():
    """过期 token → 401 HTTPException，detail 为统一 body 且 code=4011。"""
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=_expired_access_token())
    with pytest.raises(HTTPException) as exc:
        asyncio.run(get_current_user(creds))
    assert exc.value.status_code == 401
    detail = exc.value.detail
    assert isinstance(detail, dict)
    assert detail["code"] == 4011


def test_get_current_user_invalid_token_emits_4010():
    """无效 token → 401，code=4010（与过期 4011 区分）。"""
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="not.a.jwt")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(get_current_user(creds))
    assert exc.value.status_code == 401
    assert exc.value.detail == "无效或过期的 Token"


def test_get_current_user_valid_token_passes():
    creds = HTTPAuthorizationCredentials(
        scheme="Bearer", credentials=create_access_token("u1", "user")
    )
    payload = asyncio.run(get_current_user(creds))
    assert payload["sub"] == "u1"
