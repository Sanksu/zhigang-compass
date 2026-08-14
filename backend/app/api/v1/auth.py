"""认证路由：登录、刷新 Token、注册、登出、当前用户。"""

import ipaddress
import logging
from typing import Optional

from fastapi import APIRouter, Body, Depends, Request, Response
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import get_db, get_redis
from app.core.security import (
    TokenExpiredError,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models.business import AuditLog, User
from app.schemas.business import (
    ChangePasswordRequest,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    UpdateProfileRequest,
)
from app.schemas.common import ok, error

logger = logging.getLogger(__name__)

router = APIRouter()

# httpOnly Cookie 名：存 refresh_token，前端 JS 无法读取，刷新后由浏览器自动携带
REFRESH_COOKIE_NAME = "refresh_token"


def _set_refresh_cookie(response: Response, refresh_token: str) -> None:
    """将 refresh_token 写入 httpOnly Cookie（刷新页面后会话可自动恢复）。"""
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=refresh_token,
        max_age=settings.jwt_refresh_token_expire_days * 86400,
        httponly=True,
        # 生产走 HTTPS 时启用 Secure；本地开发 http 场景不加
        secure=settings.is_production,
        samesite="lax",
        path="/",
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(REFRESH_COOKIE_NAME, path="/")


def _extract_refresh_token(request: Request, req: Optional[RefreshRequest]) -> Optional[str]:
    """取 refresh_token：优先 body（兼容旧客户端），其次 httpOnly Cookie。"""
    if req and req.refresh_token:
        return req.refresh_token
    return request.cookies.get(REFRESH_COOKIE_NAME)


# 登录失败锁定（08-14 安全加固）：连续失败超限锁 15 分钟（按 ip:username 双键），
# 防暴力破解——仅凭 100/min 限流不够（批量 IP 可绕过）
_LOGIN_FAIL_LIMIT = 5
_LOGIN_FAIL_WINDOW = 900  # 15 分钟


async def _login_fail_key(redis: Redis, ip: str, username: str) -> str:
    return f"login_fail:{ip}:{username}"


async def _register_login_fail(redis: Redis, key: str) -> bool:
    """失败计数 +1；达限锁窗。返回是否已锁定。"""
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, _LOGIN_FAIL_WINDOW)
    if count >= _LOGIN_FAIL_LIMIT:
        await redis.setex(key, _LOGIN_FAIL_WINDOW, "locked")
        return True
    return False


def _client_ip(request: Request) -> str:
    """客户端 IP。

    仅生产环境信任 `X-Forwarded-For`（负载均衡终止 TLS 场景），取信任链首个
    IP（离客户端最近）；开发/直连场景取 peer IP，避免未经验证的伪造头污染审计。
    """
    if settings.is_production:
        # 与 middleware._client_ip 对齐（08-14）：逐候选校验合法 IP 才采用，
        # 非法值回退 peer IP，防伪造 XFF 头污染审计 IP
        xff = request.headers.get("x-forwarded-for", "")
        for candidate in (c.strip() for c in xff.split(",") if c.strip()):
            try:
                ipaddress.ip_address(candidate)
            except ValueError:
                continue
            return candidate
    return request.client.host if request.client else ""


@router.post("/login")
async def login(
    req: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
):
    """用户登录，返回双 Token（凭据校验走 users 表）。

    refresh_token 同时写入 httpOnly Cookie，刷新页面后前端可无感恢复会话。
    """
    fail_key = await _login_fail_key(redis, _client_ip(request), req.username)
    if await redis.get(fail_key) == "locked":
        return error(4290, "登录失败次数过多，请 15 分钟后再试", http_status=429)
    user = await db.scalar(select(User).where(User.username == req.username))
    if user is None:
        # 首次部署 bootstrap：仅当 users 表完全为空时按配置创建 admin 用户，
        # 创建后即落入 users 表，后续登录走 DB 校验；生产环境禁用该路径。
        # 08-14 安全修复：原条件为"admin 用户名不存在"——表非空时任何人可用
        # 默认配置抢注 admin（admin/admin123），改为表空才允许。
        if settings.is_production:
            return error(4010, "用户名或密码错误", http_status=401)
        user_count = await db.scalar(select(func.count()).select_from(User))
        if user_count == 0 and req.username == settings.admin_username and req.password == settings.admin_password:
            user = User(
                username=req.username,
                password_hash=hash_password(req.password),
                role="admin",
            )
            db.add(user)
            await db.commit()
            await db.refresh(user)
        else:
            await _register_login_fail(redis, fail_key)
            return error(4010, "用户名或密码错误", http_status=401)
    elif not verify_password(req.password, user.password_hash):
        await _register_login_fail(redis, fail_key)
        return error(4010, "用户名或密码错误", http_status=401)

    if not user.is_active:
        return error(4030, "账户已禁用", http_status=403)

    await redis.delete(fail_key)
    access_token = create_access_token(user.id, user.role)
    refresh_token = create_refresh_token(user.id, user.role)
    # refresh_token 写入 httpOnly Cookie（刷新页面后自动恢复会话）
    _set_refresh_cookie(response, refresh_token)
    # 写审计日志（登录成功，便于管理后台 /admin/audit/logs 追踪）
    db.add(AuditLog(
        user_id=user.id,
        action="auth.login",
        resource="users",
        resource_id=user.id,
        detail={"username": user.username},
        ip_address=_client_ip(request),
    ))
    await db.commit()
    return ok(data={
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        # 与 create_access_token 的 exp 同源（settings），防两处漂移
        "expires_in": settings.jwt_access_token_expire_minutes * 60,
    })


@router.post("/refresh")
async def refresh_token(
    request: Request,
    response: Response,
    req: RefreshRequest = Body(default=None),
    redis: Redis = Depends(get_redis),
    db: AsyncSession = Depends(get_db),
):
    """刷新 access_token。

    校验 refresh token 的 jti 是否被登出拉黑，并确认用户仍存在且未禁用。
    refresh_token 来源：body（旧客户端）或 httpOnly Cookie（刷新恢复会话）。
    每次刷新轮换 refresh_token（旧 jti 拉黑 + 重写 Cookie），降低重放风险。
    """
    refresh_token = _extract_refresh_token(request, req)
    if not refresh_token:
        return error(4010, "缺少 refresh_token", http_status=401)
    try:
        payload = decode_token(refresh_token)
    except TokenExpiredError:
        # refresh 过期（7 天 TTL）无法续期，引导重新登录（4011 专指 access 过期触发刷新）
        return error(4010, "refresh_token 已过期，请重新登录", http_status=401)
    if payload is None or payload.get("type") != "refresh":
        return error(4010, "无效的 refresh_token", http_status=401)
    jti = payload.get("jti")
    if jti:
        revoked = await redis.get(f"token:blacklist:{jti}")
        if revoked:
            return error(4010, "refresh_token 已失效", http_status=401)
    user_id = payload["sub"]
    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        return error(4010, "用户不存在或已禁用", http_status=401)
    # 轮换：旧 refresh 拉黑防重放，签发新 refresh 并重写 httpOnly Cookie
    if jti:
        await redis.set(
            f"token:blacklist:{jti}",
            "1",
            ex=settings.jwt_refresh_token_expire_days * 86400,
        )
    new_access = create_access_token(user_id, user.role)
    new_refresh = create_refresh_token(user_id, user.role)
    _set_refresh_cookie(response, new_refresh)
    return ok(data={
        "access_token": new_access,
        "refresh_token": new_refresh,
        "expires_in": settings.jwt_access_token_expire_minutes * 60,
    })


@router.post("/register")
async def register(req: RegisterRequest, db: AsyncSession = Depends(get_db)):
    """注册新用户（默认 guest 角色）。"""
    logger.info(f"[register] 收到注册请求: username={req.username}")
    if len(req.username) < 3 or len(req.password) < 6:
        logger.warning(f"[register] 参数校验失败: username={req.username}")
        return error(4000, "用户名至少 3 字符，密码至少 6 字符")

    existing = await db.scalar(select(User).where(User.username == req.username))
    if existing is not None:
        logger.warning(f"[register] 用户名已存在: username={req.username}")
        return error(4090, "用户名已存在")

    user = User(
        username=req.username,
        password_hash=hash_password(req.password),
        role="guest",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    # 写审计日志（注册成功，便于管理后台 /admin/audit/logs 追踪）
    db.add(AuditLog(
        user_id=user.id,
        action="auth.register",
        resource="users",
        resource_id=user.id,
        detail={"username": user.username},
    ))
    await db.commit()
    logger.info(f"[register] 注册成功: id={user.id} username={user.username} role={user.role}")
    return ok(data={"id": user.id, "username": user.username, "role": user.role})


@router.get("/me")
async def me(
    payload: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取当前用户信息（需登录）。"""
    user = await db.get(User, payload["sub"])
    if user is None or not user.is_active:
        return error(4040, "用户不存在")
    return ok(data={
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "email": user.email,
        "phone": user.phone,
        "bio": user.bio,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    })


@router.put("/me")
async def update_me(
    req: UpdateProfileRequest,
    request: Request,
    payload: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """更新当前用户资料（FE-M4-04 个人中心）。

    仅更新显式传入的字段（None 保持原值）；空串即清空该字段。
    """
    user = await db.get(User, payload["sub"])
    if user is None or not user.is_active:
        return error(4040, "用户不存在")
    if req.email is not None:
        user.email = req.email
    if req.phone is not None:
        user.phone = req.phone
    if req.bio is not None:
        user.bio = req.bio
    db.add(AuditLog(
        user_id=user.id,
        action="auth.update_profile",
        resource="users",
        resource_id=user.id,
        detail={"username": user.username},
        ip_address=_client_ip(request),
    ))
    await db.commit()
    return ok(data={
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "email": user.email,
        "phone": user.phone,
        "bio": user.bio,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    })


@router.post("/password")
async def change_password(
    req: ChangePasswordRequest,
    request: Request,
    payload: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """修改当前用户密码（FE-M4-04 个人中心）。

    需校验旧密码；新旧密码不可相同。
    """
    user = await db.get(User, payload["sub"])
    if user is None or not user.is_active:
        return error(4040, "用户不存在")
    if not verify_password(req.old_password, user.password_hash):
        return error(4000, "原密码错误")
    if req.old_password == req.new_password:
        return error(4000, "新密码不能与原密码相同")
    user.password_hash = hash_password(req.new_password)
    db.add(AuditLog(
        user_id=user.id,
        action="auth.change_password",
        resource="users",
        resource_id=user.id,
        detail={"username": user.username},
        ip_address=_client_ip(request),
    ))
    await db.commit()
    return ok(data={"updated": True})


@router.post("/logout")
async def logout(
    req: LogoutRequest = Body(default=None),
    request: Request = None,
    response: Response = None,
    redis: Redis = Depends(get_redis),
):
    """登出：将 refresh_token 的 jti 加入 Redis 黑名单（TTL = refresh 有效期）。

    前端同时清除本地 Token；httpOnly Cookie 中的 refresh_token 一并清除。
    refresh_token 来源：body（旧客户端）或 httpOnly Cookie。
    """
    refresh_token = _extract_refresh_token(request, req)
    # 08-14 补：拉黑 access token jti（Authorization: Bearer），登出后立即失效
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        access_token = auth_header[7:].strip()
        try:
            access_payload = decode_token(access_token)
            if access_payload and access_payload.get("jti"):
                await redis.set(
                    f"token:blacklist:{access_payload['jti']}", "1",
                    ex=settings.jwt_access_token_expire_minutes * 60,
                )
        except (TokenExpiredError, Exception):
            pass  # access 过期/非法无需拉黑，登出幂等
    try:
        payload = decode_token(refresh_token) if refresh_token else None
    except TokenExpiredError:
        # 过期 refresh 无法解析 jti 拉黑，但登出本身幂等：照常清 Cookie 即可
        payload = None
    jti = (payload or {}).get("jti")
    if jti:
        await redis.set(
            f"token:blacklist:{jti}",
            "1",
            ex=settings.jwt_refresh_token_expire_days * 86400,
        )
    _clear_refresh_cookie(response)
    return ok(data={"msg": "已登出"})
