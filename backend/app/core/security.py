"""JWT 双 Token + RBAC 权限。"""

import time
from pathlib import Path
from typing import Optional

import bcrypt
import jwt

from app.core.config import settings

# RBAC 角色 → 权限映射
ROLE_PERMISSIONS = {
    "admin":   {"*"},
    "editor":  {"graph:read", "graph:write", "data:read", "match:run"},
    "viewer":  {"graph:read", "data:read"},
    "guest":   {"graph:read"},
}


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


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


def create_refresh_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "type": "refresh",
        "iat": int(time.time()),
        "exp": int(time.time()) + settings.jwt_refresh_token_expire_days * 86400,
    }
    private_key = _load_rsa_key(settings.jwt_private_key_path)
    return jwt.encode(payload, private_key, algorithm="RS256")


def decode_token(token: str) -> Optional[dict]:
    try:
        public_key = _load_rsa_key(settings.jwt_public_key_path)
        return jwt.decode(token, public_key, algorithms=["RS256"])
    except jwt.PyJWTError:
        return None


def has_permission(role: str, permission: str) -> bool:
    perms = ROLE_PERMISSIONS.get(role, set())
    return "*" in perms or permission in perms