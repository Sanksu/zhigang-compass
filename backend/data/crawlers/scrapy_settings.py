"""Scrapy 项目设置。

业务配置（速率/代理/合规）从 crawlers.settings 导入；
此文件仅声明 Scrapy 框架级设置 + Playwright 集成 + 中间件/管道注册。
"""

import os

from crawlers.settings import (
    DEFAULT_DOWNLOAD_DELAY,
    RANDOMIZE_DOWNLOAD_DELAY,
    CONCURRENT_REQUESTS,
    CONCURRENT_REQUESTS_PER_DOMAIN,
    RETRY_TIMES,
    RETRY_HTTP_CODES,
    FEED_EXPORT_ENCODING,
    FEED_FORMAT,
)

# ── 项目标识 ──
BOT_NAME = "zhigang-compass"
SPIDER_MODULES = ["crawlers.spiders"]
NEWSPIDER_MODULE = "crawlers.spiders"

# ── 合规：遵守 robots.txt ──
ROBOTSTXT_OBEY = True

# ── 请求控制（从业务配置导入）──
DOWNLOAD_DELAY = DEFAULT_DOWNLOAD_DELAY
RANDOMIZE_DOWNLOAD_DELAY = RANDOMIZE_DOWNLOAD_DELAY
CONCURRENT_REQUESTS = CONCURRENT_REQUESTS
CONCURRENT_REQUESTS_PER_DOMAIN = CONCURRENT_REQUESTS_PER_DOMAIN

# ── 重试 ──
RETRY_TIMES = RETRY_TIMES
RETRY_HTTP_CODES = RETRY_HTTP_CODES

# ── Playwright 集成（JS 渲染）──
DOWNLOAD_HANDLERS = {
    "http": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
    "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
}
TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"
PLAYWRIGHT_BROWSER_TYPE = "chromium"
PLAYWRIGHT_LAUNCH_OPTIONS = {
    "headless": True,
}
# 国际平台走系统代理（Clash/V2Ray），通过 HTTPS_PROXY 环境变量注入
# 国内平台不设此变量即可直连
_playwright_proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
if _playwright_proxy:
    PLAYWRIGHT_LAUNCH_OPTIONS["proxy"] = {"server": _playwright_proxy}
PLAYWRIGHT_DEFAULT_NAVIGATION_TIMEOUT = 60000  # 60s（SO/Coursera/edX 广告资源多，30s 易超时）

# ── 中间件栈（UA 轮换 → 指数退避 → 代理池）──
# 重试由 Scrapy 内置 RetryMiddleware（RETRY_TIMES / RETRY_HTTP_CODES）负责；
# BackoffRetryMiddleware 优先级高于 RetryMiddleware(550)，先拦截 429/403 做指数退避
DOWNLOADER_MIDDLEWARES = {
    "crawlers.middlewares.UARotationMiddleware": 400,
    "crawlers.middlewares.ProxyPoolMiddleware": 410,
    "crawlers.middlewares.BackoffRetryMiddleware": 560,
}

# ── 管道（清洗 → 入库）──
ITEM_PIPELINES = {
    "crawlers.pipelines.CleaningPipeline": 100,
    "crawlers.pipelines.PostgresPipeline": 200,
}

# ── Feed 输出 ──
FEED_EXPORT_ENCODING = FEED_EXPORT_ENCODING
FEED_FORMAT = FEED_FORMAT

# ── 日志 ──
LOG_LEVEL = "INFO"
