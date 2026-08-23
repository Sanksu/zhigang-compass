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


def ensure_jwt_keys() -> None:
    """启动时预加载 RSA 密钥对（main.lifespan 调用）。

    密钥经 compose 挂载注入（pem 不入 git/镜像），挂载缺失时懒加载会把
    FileNotFoundError 暴露成运行时 500——登录后 refresh 报「服务器内部错误」
    且无任何线索（2026-08-22 事故：容器按旧 compose 创建缺少 keys 挂载）。
    启动阶段 fail-fast，报错直接指向挂载修复动作。
    """
    try:
        _load_rsa_key(settings.jwt_private_key_path)
        _load_rsa_key(settings.jwt_public_key_path)
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"JWT 签名密钥缺失（{exc.filename}）：密钥不入镜像，经 compose 挂载 "
            "backend/keys:/app/keys 注入；确认宿主 backend/keys/ 下 private.pem 与 "
            "public.pem 存在后执行 docker compose up -d --force-recreate api worker"
        ) from exc


def create_access_token(user_id: str, role: str) -> str:
    payload = {
        "sub": user_id,
        "role": role,
        "type": "access",
        "aud": settings.jwt_audience,  # audience（08-15 中危修复：防 token 跨服务复用）
        "jti": uuid.uuid4().hex,  # 登出黑名单依据（08-14 补：此前仅 refresh 有 jti，登出后 access 仍有效至过期）
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
        "aud": settings.jwt_audience,
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
        return jwt.decode(token, public_key, algorithms=["RS256"], audience=settings.jwt_audience)
    except jwt.ExpiredSignatureError:
        raise TokenExpiredError("Token 已过期") from None
    except jwt.PyJWTError:
        return None


def has_permission(role: str, permission: str) -> bool:
    perms = ROLE_PERMISSIONS.get(role, set())
    return "*" in perms or permission in perms
