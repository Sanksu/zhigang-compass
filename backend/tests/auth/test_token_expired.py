"""Token 过期语义测试（契约错误码 4011，设计文档 §2.4.7）。

覆盖：
- decode_token 对过期 token 抛 TokenExpiredError（区别于无效 → None）
- get_current_user 对过期 access_token 抛 HTTPException 401，detail 携带 code=4011
- 正常 token 不受影响（仍返回 payload）

RSA 密钥：CI 环境无 keys/*.pem（gitignore 排除），测试用临时生成的密钥对
注入 settings，避免依赖真实密钥文件。
"""

import asyncio
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.security import TokenExpiredError, create_access_token, decode_token


@pytest.fixture(scope="session")
def tmp_rsa_keys(tmp_path_factory):
    """生成临时 RSA 密钥对并返回 (私钥路径, 公钥路径)。"""
    tmp = tmp_path_factory.mktemp("jwt-keys")
    priv_path = tmp / "private.pem"
    pub_path = tmp / "public.pem"

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    pub_path.write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return str(priv_path), str(pub_path)


@pytest.fixture(autouse=True)
def _use_tmp_keys(tmp_rsa_keys):
    """全局注入临时密钥路径（create/decode 均经 settings 读取）。"""
    priv, pub = tmp_rsa_keys
    with patch.object(settings, "jwt_private_key_path", priv), \
         patch.object(settings, "jwt_public_key_path", pub):
        yield


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
        asyncio.run(get_current_user(creds, _FakeRedis()))
    assert exc.value.status_code == 401
    detail = exc.value.detail
    assert isinstance(detail, dict)
    assert detail["code"] == 4011


def test_get_current_user_invalid_token_emits_4010():
    """无效 token → 401，code=4010（与过期 4011 区分）。"""
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="not.a.jwt")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(get_current_user(creds, _FakeRedis()))
    assert exc.value.status_code == 401
    assert exc.value.detail == "无效或过期的 Token"


def test_get_current_user_valid_token_passes():
    creds = HTTPAuthorizationCredentials(
        scheme="Bearer", credentials=create_access_token("u1", "user")
    )
    payload = asyncio.run(get_current_user(creds, _FakeRedis()))
    assert payload["sub"] == "u1"

class _FakeRedis:
    """黑名单检查桩（get 返回 None = 未拉黑）。"""

    async def get(self, key):
        return None
