"""爬虫基类：统一搜索关键字/城市配置 + 合规声明 + JobItem 构造。"""

import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from scrapy_playwright.page import PageMethod
from scrapy import Request, Spider
from scrapy.http import Response

from crawlers.settings import RATE_LIMIT, MAIMAI_COMPLIANCE, SUBPROCESS_TIMEOUT
from crawlers.items import JobItem


# 默认搜索关键字：空 = 按平台热度/最新采集（08-16 用户决策，不再内置定向词）。
# 前端手动触发时通过 -a keywords= 显式指定
DEFAULT_KEYWORDS: list[str] = []

# 默认搜索城市：空 = 不限城市（08-16 用户决策）；前端手动触发时通过 -a cities= 指定
DEFAULT_CITIES: list[str] = []


# 课程源浏览器渲染请求的 UA（coursera/edx 原两份相同 dict）
_BROWSER_UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}


def make_playwright_request(
    url: str,
    meta: dict,
    selector: str,
    *,
    wait_timeout: int = 20000,
    scroll_times: int = 0,
    scroll_wait_ms: int = 3000,
    callback=None,
    headers: dict | None = None,
) -> Request:
    """构造 Playwright 渲染请求（08-17 收敛 coursera/zhilian/edx 三处同构）。

    等待 selector 出现（timeout=wait_timeout）后按 scroll_times 次滚动到底部
    触发懒加载（每次间隔 scroll_wait_ms；0 则不等）。headers 缺省用课程源 UA。
    """
    methods = [PageMethod("wait_for_selector", selector, timeout=wait_timeout)]
    for _ in range(max(0, scroll_times)):
        methods.append(PageMethod("evaluate", "window.scrollTo(0, document.body.scrollHeight)"))
        if scroll_wait_ms > 0:
            methods.append(PageMethod("wait_for_timeout", scroll_wait_ms))

    # playwright_context_kwargs.user_agent：headers 只作用于初始导航请求，
    # 浏览器 context 的真实 UA 才是源站风控（EdgeOne 等）判定依据——默认
    # HeadlessChrome UA 会被拦（08-23 zhilian 改版实测 selector 恒超时）
    return Request(
        url,
        callback=callback,
        meta={
            "playwright": True,
            "playwright_page_methods": methods,
            "playwright_context_kwargs": {"user_agent": _BROWSER_UA["User-Agent"]},
            **meta,
        },
        headers=_BROWSER_UA if headers is None else headers,
        dont_filter=True,
    )


def run_script(cmd: list[str], cwd: str, logger, label: str) -> tuple[str, str, int] | None:
    """执行采集脚本：Popen + 超时终止（08-17 收敛 6 处相同子进程样板）。

    启动失败 / 超时返回 None（调用方自行 continue/return）；
    成功返回 (stdout, stderr, returncode)，退出码/错误流供调用方记录 CDP 日志。
    """
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            cwd=cwd,
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
        )
    except Exception as e:
        logger.error(f"启动采集脚本失败: {e}")
        return None
    # 阻塞读取子进程输出（stdout/stderr 一并读取避免管道死锁），超时后终止
    try:
        stdout, stderr = proc.communicate(timeout=SUBPROCESS_TIMEOUT)
        return stdout, stderr, proc.returncode
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        logger.error(f"{label} 超时（>{SUBPROCESS_TIMEOUT}s），已终止")
        return None


def iter_jsonl(stdout: str, logger):
    """逐行解析采集脚本 JSONL 输出；损坏行记日志跳过（08-17 收敛 6 处相同解析循环）。"""
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError as e:
            logger.error(f"JSONL 解析失败: {e}, line={line[:100]}")


class BaseSpider(Spider):
    """所有爬虫的共享基类。

    自动处理：
    - 速率限制（从 RATE_LIMIT 读取）
    - 搜索关键字/城市遍历（支持 -a keywords=Python,Java 覆盖）
    - 脉脉合规声明注入
    - JobItem 统一构造
    """

    platform: str = ""  # 子类必须设置：boss / zhilian / monster / ...

    # 子类可覆盖：搜索关键字与城市列表
    keywords: list[str] = DEFAULT_KEYWORDS
    cities: list[str] = DEFAULT_CITIES

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 平台级限速接线：从 RATE_LIMIT.delay_range 取中点设置
        # download_delay，与 arxiv/coursera 等非招聘源一致；避免招聘爬虫走
        # 全局默认 2s（折算 ~40 req/min）超出 settings.py 声明的 20 req/min 上限
        self.limit = RATE_LIMIT.get(self.platform, {})
        delay_range = self.limit.get("delay_range")
        if delay_range and len(delay_range) == 2:
            self.download_delay = sum(delay_range) / 2
        # 支持 -a keywords=Python,Java -a cities=北京,上海 运行时覆盖
        if kwargs.get("keywords"):
            self.keywords = kwargs["keywords"].split(",")
        if kwargs.get("cities"):
            self.cities = kwargs["cities"].split(",")

    def start_requests(self):
        raise NotImplementedError("子类必须实现 start_requests")

    async def start(self):
        """Scrapy 2.13+ 入口：桥接到子类的 start_requests。

        保留 start_requests 是为了让子类用同步 generator 写法更直观，
        且便于单元测试直接调用 start_requests()。
        """
        for request in self.start_requests():
            yield request

    def parse(self, response: Response):
        raise NotImplementedError("子类必须实现 parse")

    def _compliance_headers(self) -> dict:
        """脉脉合规声明头。"""
        if self.platform == "maimai":
            return {"X-Collection-Purpose": MAIMAI_COMPLIANCE["annotation"]}
        return {}

    def make_item(self, **fields) -> JobItem:
        """构造 JobItem，自动填充 source / crawled_at / 合规标记。"""
        item = JobItem()
        item["source"] = self.platform
        item["crawled_at"] = datetime.now(timezone(timedelta(hours=8))).isoformat()
        item["is_desensitized"] = False
        for k, v in fields.items():
            if k in item.fields:
                item[k] = v
        return item

    @staticmethod
    def build_query(params: dict) -> str:
        """构造 URL query string。"""
        return urlencode(params)
