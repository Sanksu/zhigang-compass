"""中国大学MOOC 爬虫（icourse163，国内学习路径数据源）。

策略：
- 通过 subprocess 调用独立 Playwright 脚本 icourse163_crawler.py
  （脚本内用 Playwright 启动 headless Chromium，先导航到 search.htm 建立会话，
   然后在页面上下文内 fetch 调用内部 RPC API searchCourse.rpc）
- 解析 JSONL 输出并 yield CourseItem
- 国内直连，无需代理

合规：
- 仅采集公开课程元数据（标题/讲师/院校/注册数/标签）
- 每周全量同步，请求间隔 8-15s
- 不绕过登录态（icourse163 搜索页本身是公开的）

运行：
  scrapy crawl icourse163 -a keywords=Python,机器学习,人工智能 -o output/icourse163.jsonl
"""

import os
import sys
import time
from datetime import datetime, timedelta, timezone

from scrapy import Request, Spider
from scrapy.http import Response

from crawlers.base_spider import iter_jsonl, run_script
from crawlers.items import CourseItem
from crawlers.settings import RATE_LIMIT


# 默认搜索关键词：空 = 平台默认课程流（08-16 用户决策，不再内置定向词）
DEFAULT_KEYWORDS: list[str] = []


class Icourse163Spider(Spider):
    """中国大学MOOC 采集。

    不继承 BaseSpider（非岗位数据），直接继承 Spider。
    """

    name = "icourse163"
    platform = "icourse163"

    custom_settings = {
        "ROBOTSTXT_OBEY": False,
        "DOWNLOAD_DELAY": 0,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # -a keywords=Python,机器学习 覆盖默认关键词
        kws = kwargs.get("keywords")
        self.keywords = kws.split(",") if kws else DEFAULT_KEYWORDS
        # -a max_pages=3 控制单关键词翻页数；非法输入（-a max_pages=abc）回退默认 3
        try:
            self.max_pages = int(kwargs.get("max_pages", "3"))
        except (TypeError, ValueError):
            self.max_pages = 3
        # 请求间隔（仅用于日志展示，实际延迟在脚本内）
        limit = RATE_LIMIT.get(self.platform, {})
        delay_range = limit.get("delay_range", (8, 15))
        self.download_delay = sum(delay_range) / 2
        # 采集脚本路径
        self.crawler_script = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "icourse163_crawler.py",
        )

    async def start(self):
        """Scrapy 2.13+ 入口：桥接到 start_requests。"""
        for request in self.start_requests():
            yield request

    def start_requests(self):
        # 用 search.htm 作为占位请求（已知返回 200），触发 parse 调用采集脚本
        yield Request(
            "https://www.icourse163.org/search.htm?search=_placeholder",
            callback=self.parse,
            meta={"keywords": self.keywords},
            dont_filter=True,
            errback=self._on_error,
        )

    async def parse(self, response: Response):
        """通过 subprocess 调用独立采集脚本，解析 JSONL 输出并 yield Item。"""
        # 空关键词 = 平台默认课程流（08-16 用户决策）
        keywords = (response.meta.get("keywords") or self.keywords) or [""]

        python_exe = sys.executable
        keyword_total = len(keywords)
        _started = time.monotonic()

        for kw_idx, keyword in enumerate(keywords):
            self.logger.info(f"[icourse163] 进度 {kw_idx + 1}/{keyword_total}（已用 {time.monotonic() - _started:.0f}s）: 开始采集 关键词={keyword}")

            cmd = [
                python_exe, self.crawler_script,
                "--keyword", keyword,
                "--max-pages", str(self.max_pages),
            ]

            result = run_script(cmd, os.path.dirname(self.crawler_script), self.logger,
                               f"[icourse163] 任务 {kw_idx + 1}/{keyword_total}")
            if result is None:
                continue
            stdout, stderr_output, returncode = result

            count = 0
            for item_data in iter_jsonl(stdout, self.logger):
                yield self._make_item(item_data)
                count += 1

            if returncode != 0:
                self.logger.error(
                    f"采集脚本退出码 {returncode}, stderr: {stderr_output[-300:]}"
                )
            else:
                # 把 stderr 的关键日志也转记一下（便于排错）
                for stderr_line in stderr_output.splitlines():
                    if stderr_line:
                        self.logger.info(f"[script] {stderr_line}")

            self.logger.info(f"[icourse163] 进度 {kw_idx + 1}/{keyword_total}: 关键词={keyword} 采集完成，共 {count} 条")

    def _on_error(self, failure):
        """占位请求失败回调（正常情况，本地 1 端口不通）。"""
        self.logger.info("占位请求触发（预期行为），开始调用采集脚本")

    def _make_item(self, data: dict) -> CourseItem:
        """把脚本输出的 dict 转为 CourseItem。"""
        item = CourseItem()
        item["source"] = self.platform
        item["source_id"] = data.get("source_id", "")
        item["source_url"] = data.get("source_url", "")
        item["crawled_at"] = datetime.now(timezone(timedelta(hours=8))).isoformat()
        item["title"] = data.get("title", "")
        item["instructor"] = data.get("instructor", "")
        item["institution"] = data.get("institution", "")
        item["platform"] = "icourse163"
        item["category"] = data.get("category", "")
        item["description"] = data.get("description", "")
        item["rating"] = float(data.get("rating", 0.0))
        item["enrollment"] = int(data.get("enrollment", 0))
        item["duration"] = data.get("duration", "")
        item["start_date"] = data.get("start_date", "")
        item["skills"] = data.get("skills", [])
        item["raw_text"] = data.get("raw_text", "")
        item["is_desensitized"] = False
        return item
