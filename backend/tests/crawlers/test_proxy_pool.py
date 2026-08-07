"""ProxyPoolMiddleware 代理池来源测试。

覆盖：显式 PROXY_POOL 优先、环境变量 HTTPS_PROXY/HTTP_PROXY 回退、
无任何配置时回退开发默认代理 DEFAULT_PROXY（scrapy Twisted 下载器不读
环境变量，中间件负责注入 request.meta["proxy"]）。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "data"))

from crawlers.middlewares import ProxyPoolMiddleware
from crawlers.settings import DEFAULT_PROXY


def test_env_proxy_fallback(monkeypatch):
    """PROXY_POOL 为空时回退 HTTPS_PROXY 环境变量。"""
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    m = ProxyPoolMiddleware()
    assert m._pick_proxy() == "http://127.0.0.1:7890"


def test_http_proxy_fallback(monkeypatch):
    """仅 HTTP_PROXY 时同样回退（HTTPS_PROXY 优先的兜底路径）。"""
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7890")
    m = ProxyPoolMiddleware()
    assert m._pick_proxy() == "http://127.0.0.1:7890"


def test_default_proxy_fallback(monkeypatch):
    """无显式配置且无环境变量代理时回退开发默认代理（ARQ worker 场景）。"""
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    m = ProxyPoolMiddleware()
    assert m._pick_proxy() == DEFAULT_PROXY
