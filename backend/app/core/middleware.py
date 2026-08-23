"""CORS / CSP / HSTS / GZip / TraceID / 限流中间件。"""

import contextvars
import ipaddress
import uuid

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
        """生产信任 X-Forwarded-For（负载均衡终止 TLS），但仅接受合法 IP。

        攻击者可伪造 XFF 头；逐项校验为合法 IP 才采用，非法值回退 peer IP，
        防止伪造垃圾值污染限流键空间（仍无法防 IP 伪造，限流为增强能力）。
        """
        if settings.is_production:
            xff = request.headers.get("x-forwarded-for", "")
            for candidate in (c.strip() for c in xff.split(",") if c.strip()):
                try:
                    ipaddress.ip_address(candidate)
                except ValueError:
                    continue
                return candidate
        return request.client.host if request.client else ""

    @staticmethod
    def _is_llm_route(path: str) -> bool:
        # 同步 LLM 生成端点：诊断报告（/match/result/{id}/diagnosis）
        return "/match/" in path and path.endswith("/diagnosis")

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        # 仅限流 API 路由（认证端同样纳入，防恶意批量注册/暴力破解）；静态资源放行
        if not path.startswith("/api/v1"):
            return await call_next(request)
        ip = self._client_ip(request)
        if not ip:
            return await call_next(request)
        limit = self.LLM_LIMIT if self._is_llm_route(path) else self.GENERAL_LIMIT
        # 限流键收敛到模块级（08-14 修复）：完整路径含资源 ID（graph/skill/{id}/positions
        # 等），遍历 ID 可每 key 独立计数绕过 100/min；按 /api/v1/{module} 聚合，
        # LLM 端点独立键（10/min）
        parts = path.strip("/").split("/")
        module = "llm" if self._is_llm_route(path) else (parts[2] if len(parts) > 2 else path)
        key = f"rate:{ip}:{module}"
        try:
            # 原子窗口：SET NX EX 创建计数（08-16 审查：原 incr+expire 两步在
            # Redis 瞬时错误时可能留下无 TTL 的 key，该 IP+模块永久 429）
            created = await redis_client.set(key, 1, nx=True, ex=self.WINDOW_SECONDS)
            count = 1 if created else await redis_client.incr(key)
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
        # style-src 'unsafe-inline'：ECharts tooltip 经 innerHTML + style 属性渲染，
        # 严格 style-src 会拦截其内联样式导致 tooltip 不显示（08-16 修复，回退 default-src
        # 后 ECharts 走 canvas tooltip 层）。tooltip 内容已在前端 formatter 做 escapeHtml，
        # 且 script-src 已含 'unsafe-inline'，style 放行不新增脚本类攻击面。
        # font-src data:：图标字体以 base64 data URI 内联打包，需放行。
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "font-src 'self' data:; "
            "worker-src 'self' blob:; "
            "img-src 'self' data: https:"
        )

        if settings.is_production:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

        response.headers["X-Trace-ID"] = trace_id

        return response
