"""安全响应头中间件单测（08-16：CSP 曾拦截 ECharts tooltip 内联样式 / data: 字体）。

策略断言以"放行图谱可视化所需、保留其余限制"为口径：
- style-src 'unsafe-inline'：ECharts HTML tooltip 必需（内容已 escapeHtml）
- font-src data:：base64 内联图标字体必需
- script-src / worker-src / img-src 保持既有限制（设计文档 §11.4 契约）
"""

import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse

from app.core.middleware import SecurityHeadersMiddleware


def _app():
    app = Starlette()
    app.add_middleware(SecurityHeadersMiddleware)

    async def _root(request):
        return PlainTextResponse("ok")

    app.add_route("/", _root)
    return app


class TestCSPHeaders:
    @pytest.mark.asyncio
    async def test_tooltip_inline_style_allowed(self):
        """ECharts tooltip 内联样式放行（style-src 'unsafe-inline'）。"""
        transport = httpx.ASGITransport(app=_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            csp = (await client.get("/")).headers["content-security-policy"]
        assert "style-src 'self' 'unsafe-inline'" in csp

    @pytest.mark.asyncio
    async def test_inline_font_allowed(self):
        """base64 内联字体放行（font-src data:）。"""
        transport = httpx.ASGITransport(app=_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            csp = (await client.get("/")).headers["content-security-policy"]
        assert "font-src 'self' data:" in csp

    @pytest.mark.asyncio
    async def test_script_worker_img_restrictions_kept(self):
        """既有脚本/worker/图片限制不回退 default-src。"""
        transport = httpx.ASGITransport(app=_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            csp = (await client.get("/")).headers["content-security-policy"]
        assert "script-src 'self' 'unsafe-inline'" in csp
        assert "worker-src 'self' blob:" in csp
        assert "img-src 'self' data: https:" in csp
