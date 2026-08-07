"""Scrapy 共享中间件：UA 轮换 / 代理池 / 指数退避。"""

import os
import random
import time
from threading import Lock
from urllib.parse import urlsplit

import requests

from crawlers.settings import (
    DEFAULT_PROXY,
    POOL_REQUIRED,
    PROXY_POOL,
    PROXY_POOL_REFRESH_INTERVAL,
    PROXY_POOL_API_URL,
    PROXY_POOL_API_KEY,
    PROXY_MAX_FAILURES,
)


class UARotationMiddleware:
    """User-Agent 轮换。"""

    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/18.2 Safari/605.1.15",
    ]

    def process_request(self, request):
        request.headers.setdefault("User-Agent", random.choice(self.USER_AGENTS))


class ProxyPoolMiddleware:
    """代理池中间件。

    从 PROXY_POOL 列表中随机选取代理分配给国际平台请求；
    代理失败次数超过上限后自动剔除；
    支持定时通过 API 刷新代理列表。

    注意：scrapy 2.12+ 下载中间件不再传 spider 参数（已废弃），
    经 from_crawler 保存 crawler，用 self.crawler.spider 访问。
    """

    def __init__(self):
        self.crawler = None
        self._pool = self._init_pool()          # 可用代理
        self._failures: dict[str, int] = {}      # 代理 → 连续失败次数
        self._lock = Lock()
        self._last_refresh = 0.0

    @classmethod
    def from_crawler(cls, crawler):
        mw = cls()
        mw.crawler = crawler
        return mw

    def _spider(self):
        """当前 spider（下载中间件签名不再接收 spider 参数）。"""
        return self.crawler.spider if self.crawler is not None else None

    def _should_proxy(self, request) -> bool:
        """是否对该请求分配代理。

        排除两类请求：
        - 本地 CDP 占位请求（monster/glassdoor 连 127.0.0.1:9222），代理会把它路由到远程
        - Playwright 请求：scrapy-playwright 不读 meta["proxy"]，代理由
          PLAYWRIGHT_LAUNCH_OPTIONS["proxy"]（环境变量）控制，分配了也不生效
        """
        if request.meta.get("playwright"):
            return False
        host = urlsplit(request.url).hostname or ""
        if host in ("localhost", "::1") or host.startswith("127."):
            return False
        return True

    def process_request(self, request):
        spider = self._spider()
        if spider is None or spider.name not in POOL_REQUIRED:
            return
        if not self._should_proxy(request):
            return

        self._ensure_pool(spider)
        proxy = self._pick_proxy()
        if proxy:
            request.meta["proxy"] = proxy
            spider.logger.debug(f"[ProxyPool] 使用代理: {proxy}")
        else:
            spider.logger.warning("[ProxyPool] 无可用代理，跳过代理")

    def process_response(self, request, response):
        spider = self._spider()
        if spider is not None and spider.name in POOL_REQUIRED and response.status in (403, 429, 502, 503):
            proxy = request.meta.get("proxy", "")
            self._mark_failure(proxy, spider)
        return response

    def process_exception(self, request, exception):
        spider = self._spider()
        if spider is not None and spider.name in POOL_REQUIRED:
            proxy = request.meta.get("proxy", "")
            self._mark_failure(proxy, spider)
        return None

    # ---- internal ----

    @staticmethod
    def _env_proxies() -> list[str]:
        """环境变量代理（HTTPS_PROXY 优先，HTTP_PROXY 兜底）。

        scrapy 的 Twisted 下载器不读环境变量，需经此注入 request.meta["proxy"]；
        与 settings.py 契约及各国 spider 文档（HTTPS_PROXY=http://127.0.0.1:7890）一致。
        """
        return [p for p in (os.environ.get("HTTPS_PROXY"), os.environ.get("HTTP_PROXY")) if p]

    def _init_pool(self) -> list[str]:
        """代理池初始化：显式配置 → 环境变量 → 开发默认代理。"""
        return list(PROXY_POOL) or self._env_proxies() or ([DEFAULT_PROXY] if DEFAULT_PROXY else [])

    def _ensure_pool(self, spider):
        if time.time() - self._last_refresh < PROXY_POOL_REFRESH_INTERVAL:
            return
        self._last_refresh = time.time()

        # 优先从 API 获取
        if PROXY_POOL_API_URL:
            try:
                resp = requests.get(
                    PROXY_POOL_API_URL,
                    headers={"Authorization": f"Bearer {PROXY_POOL_API_KEY}"},
                    timeout=10,
                )
                if resp.ok:
                    proxies = [p.strip() for p in resp.json() if p.strip()]
                    with self._lock:
                        self._pool = proxies
                    spider.logger.info(f"[ProxyPool] 从 API 刷新代理池: {len(proxies)} 个")
                    return
            except Exception as e:
                spider.logger.warning(f"[ProxyPool] API 刷新失败: {e}")

        # API 不可用时使用静态池/环境变量代理
        with self._lock:
            if not self._pool:
                self._pool = self._init_pool()
                if self._pool:
                    spider.logger.info(f"[ProxyPool] 使用静态代理池: {len(self._pool)} 个")

    def _pick_proxy(self) -> str | None:
        with self._lock:
            return random.choice(self._pool) if self._pool else None

    def _mark_failure(self, proxy: str, spider):
        if not proxy:
            return
        with self._lock:
            self._failures[proxy] = self._failures.get(proxy, 0) + 1
            if self._failures[proxy] >= PROXY_MAX_FAILURES:
                if proxy in self._pool:
                    self._pool.remove(proxy)
                    spider.logger.warning(f"[ProxyPool] 剔除代理 {proxy}（连续失败 {PROXY_MAX_FAILURES} 次），剩余 {len(self._pool)} 个")
