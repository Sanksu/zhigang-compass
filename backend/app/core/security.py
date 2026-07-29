"""JWT 双 Token + RBAC 权限。"""

import time
from typing import Optional

import jwt
from passlib.context import CryptContext

from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# RBAC 角色 → 权限映射
ROLE_PERMISSIONS = {
    "admin":   {"*"},
    "editor":  {"graph:read", "graph:write", "data:read", "match:run"},
    "viewer":  {"graph:read", "data:read"},
    "guest":   {"graph:read"},
}


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(user_id: str, role: str) -> str:
    payload = {
        "sub": user_id,
        "role": role,
        "type": "access",
        "iat": int(time.time()),
        "exp": int(time.time()) + settings.jwt_access_token_expire_minutes * 60,
    }
    return jwt.encode(payload, settings.jwt_private_key, algorithm="RS256")


def create_refresh_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "type": "refresh",
        "iat": int(time.time()),
        "exp": int(time.time()) + settings.jwt_refresh_token_expire_days * 86400,
    }
    return jwt.encode(payload, settings.jwt_private_key, algorithm="RS256")


def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, settings.jwt_public_key, algorithms=["RS256"])
    except jwt.PyJWTError:
        return None


def has_permission(role: str, permission: str) -> bool:
    perms = ROLE_PERMISSIONS.get(role, set())
    return "*" in perms or permission in perms
