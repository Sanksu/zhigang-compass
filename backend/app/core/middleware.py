"""CORS / CSP / HSTS / GZip / TraceID / 限流中间件。"""

import contextvars
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import settings
from app.core.database import redis_client

# 请求级 Trace ID 上下文，供 ok()/error() 注入响应体
trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "trace_id", default=""
)


def setup_middleware(app: FastAPI) -> None:
    """按顺序注册所有中间件。"""

    # CORS — 可配置白名单模式；通配 origin（开发默认）时不允许携带凭据
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials="*" not in settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # GZip — 响应体 > 1KB 自动压缩
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    # 限流 — 令牌桶（设计文档 §1.4.1/§11.2，错误码 4290）
    app.add_middleware(RateLimitMiddleware)

    # CSP / HSTS — 自定义响应头
    app.add_middleware(SecurityHeadersMiddleware)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """按 (IP, 路由) 的滑动窗口限流。

    普通接口 100 req/min，LLM 生成类（诊断报告）10 req/min（设计文档 §1.4.1）。
    键空间 `rate:{ip}:{path}` 对齐设计文档 §11.4.4。Redis 不可用时降级放行
    （限流是增强能力，不拖垮 API）。
    """

    GENERAL_LIMIT = 100
    LLM_LIMIT = 10
    WINDOW_SECONDS = 60

    @staticmethod
    def _client_ip(request: Request) -> str:
        """生产信任 X-Forwarded-For（负载均衡终止 TLS），开发取 peer IP。"""
        if settings.is_production:
            xff = request.headers.get("x-forwarded-for", "")
            if xff:
                return xff.split(",")[0].strip()
        return request.client.host if request.client else ""

    @staticmethod
    def _is_llm_route(path: str) -> bool:
        # 同步 LLM 生成端点：诊断报告（/match/result/{id}/diagnosis）
        return "/match/" in path and path.endswith("/diagnosis")

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        # 仅限流 API 路由；认证端（登录/注册/刷新）、健康检查与静态资源放行
        if not path.startswith("/api/v1") or path.startswith("/api/v1/auth/"):
            return await call_next(request)
        ip = self._client_ip(request)
        if not ip:
            return await call_next(request)
        limit = self.LLM_LIMIT if self._is_llm_route(path) else self.GENERAL_LIMIT
        key = f"rate:{ip}:{path}"
        try:
            count = await redis_client.incr(key)
            if count == 1:
                await redis_client.expire(key, self.WINDOW_SECONDS)
        except Exception:
            return await call_next(request)
        if count > limit:
            # 手动构造统一响应体（避免 import schemas.common 造成循环依赖）
            return JSONResponse(
                status_code=429,
                content={
                    "code": 4290,
                    "msg": "请求过于频繁，请稍后再试",
                    "data": None,
                    "trace_id": trace_id_var.get(""),
                },
            )
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Trace ID：服务端生成（不信任客户端传入），供响应头与 ok()/error() 注入响应体
        trace_id = str(uuid.uuid4().hex[:16])
        trace_id_var.set(trace_id)

        response = await call_next(request)

        # CSP
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "worker-src 'self' blob:; "
            "img-src 'self' data: https:"
        )

        if settings.is_production:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

        response.headers["X-Trace-ID"] = trace_id

        return response
