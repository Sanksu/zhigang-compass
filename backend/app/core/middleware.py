"""CORS / CSP / HSTS / GZip / TraceID 中间件。"""

import contextvars
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import settings

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

    # CSP / HSTS — 自定义响应头
    app.add_middleware(SecurityHeadersMiddleware)


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
