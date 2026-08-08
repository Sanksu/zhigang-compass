"""JWT 双 Token + RBAC 权限。"""

import time
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Optional

import bcrypt
import jwt

from app.core.config import settings

# RBAC 角色 → 权限映射（角色集合与 admin.py / 前端 constants / openapi 统一：admin/user/guest）
ROLE_PERMISSIONS = {
    "admin": {"*"},
    "user":  {"graph:read", "graph:write", "data:read", "match:run"},
    "guest": {"graph:read"},
}


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


@lru_cache(maxsize=2)
def _load_rsa_key(key_path: str) -> str:
    """加载 RSA 密钥，支持相对于 backend/ 目录的路径。"""
    p = Path(key_path)
    if not p.is_absolute():
        p = Path(__file__).resolve().parent.parent.parent / key_path
    return p.read_text()


def create_access_token(user_id: str, role: str) -> str:
    payload = {
        "sub": user_id,
        "role": role,
        "type": "access",
        "iat": int(time.time()),
        "exp": int(time.time()) + settings.jwt_access_token_expire_minutes * 60,
    }
    private_key = _load_rsa_key(settings.jwt_private_key_path)
    return jwt.encode(payload, private_key, algorithm="RS256")


def create_refresh_token(user_id: str, role: str = "guest") -> str:
    payload = {
        "sub": user_id,
        "role": role,
        "type": "refresh",
        "jti": uuid.uuid4().hex,  # 登出黑名单依据（TTL = refresh 有效期）
        "iat": int(time.time()),
        "exp": int(time.time()) + settings.jwt_refresh_token_expire_days * 86400,
    }
    private_key = _load_rsa_key(settings.jwt_private_key_path)
    return jwt.encode(payload, private_key, algorithm="RS256")


class TokenExpiredError(Exception):
    """JWT 过期（区别于无效/伪造，契约错误码 4011）。

    decode_token 对过期 token 抛此异常，让调用方能区分「过期→触发刷新」与
    「非法→重新认证」两种 401 语义（设计文档 §2.4.7）。
    """


def decode_token(token: str) -> Optional[dict]:
    try:
        public_key = _load_rsa_key(settings.jwt_public_key_path)
        return jwt.decode(token, public_key, algorithms=["RS256"])
    except jwt.ExpiredSignatureError:
        raise TokenExpiredError("Token 已过期") from None
    except jwt.PyJWTError:
        return None


def has_permission(role: str, permission: str) -> bool:
    perms = ROLE_PERMISSIONS.get(role, set())
    return "*" in perms or permission in perms