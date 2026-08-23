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
# 代理注入按 spider 门控（08-23 修复）：此前全局读 HTTPS_PROXY 注入，国内
# Playwright 源（zhilian 智联每日主源）也硬依赖代理可达——Linux Docker 不解析
# host.docker.internal 时智联全灭。scrapy-playwright 0.0.48 不读
# request.meta["proxy"]，代理只能走启动参数；国际 Playwright 源（coursera）
# 经自身 custom_settings 调 playwright_launch_options() 注入，国内源继承此处直连。


def playwright_launch_options(env=None) -> dict:
    """国际 Playwright 源的浏览器启动参数：env 代理非空时注入 proxy。

    env 缺省取 os.environ；测试可传自定义映射。空值视为未设置（直连）。
    """
    env = os.environ if env is None else env
    proxy = env.get("HTTPS_PROXY") or env.get("HTTP_PROXY")
    options = {"headless": True}
    if proxy:
        options["proxy"] = {"server": proxy}
    return options
PLAYWRIGHT_DEFAULT_NAVIGATION_TIMEOUT = 60000  # 60s（SO/Coursera/edX 广告资源多，30s 易超时）

# ── 中间件栈（UA 轮换 → 代理池 → 指数退避）──
# 重试由 Scrapy 内置 RetryMiddleware（RETRY_TIMES / RETRY_HTTP_CODES）负责；
# BackoffRetryMiddleware 优先级高于 RetryMiddleware(550)，先拦截 429/403 做指数退避。
# ProxyPoolMiddleware(570) 需在 BackoffRetry(560) 之前收到 429/403 响应（响应链
# 从高序号到低序号）：BackoffRetry 对 429/403 返回 None 会中断响应链，此前
# ProxyPool(410) 收不到状态码，死代理剔除滞后（08-14 审查修复）
DOWNLOADER_MIDDLEWARES = {
    "crawlers.middlewares.UARotationMiddleware": 400,
    "crawlers.middlewares.ProxyPoolMiddleware": 570,
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
