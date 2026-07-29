"""BOSS 直聘爬虫（A 级 — 稳定源）。

策略（参考 github.com/eatmoreduck/boss-zhipin-scraper 的 CDP 方案）：
- 通过 Playwright `connect_over_cdp` 连接用户已登录的真实 Chrome/Edge
- 复用真实浏览器指纹和登录态，绕过 BOSS 风控
- 在已登录页面上下文内执行 fetch 调用内部 API
  `/wapi/zpgeek/search/joblist.json` 获取明文薪资
- 翻页间隔 12-22 秒，单次最多 5 页

⚠️ 合规声明：
- 仅采集公开搜索 API 返回的数据，不绕过登录态
- 仅用于竞赛演示不商用
- 用户需主动在 Chrome 中登录 BOSS 直聘，本爬虫不自动登录
- 采集数据已脱敏（CleaningPipeline），不含 PII

使用方式：
  # 1. 启动隔离 Chrome 并登录 BOSS（只需一次，登录态持久保存）
  python -m crawlers.setup_boss_chrome

  # 2. 检查登录态是否有效
  python -m crawlers.setup_boss_chrome --check

  # 3. 运行爬虫（保持 Chrome 开启）
  scrapy crawl boss -a keywords=Python -a cities=北京 -o output/boss.jsonl

技术说明：
  通过 subprocess 调用独立采集脚本 boss_cdp_crawler.py，避免 Playwright
  与 Scrapy Twisted 事件循环不兼容的问题。脚本输出 JSONL 到 stdout，
  Spider 解析后 yield JobItem。
"""

import json
import os
import subprocess
import sys
from urllib.parse import urlencode

from scrapy import Request
from scrapy.http import Response

from crawlers.base_spider import BaseSpider

# BOSS 直聘城市代码映射
BOSS_CITY_CODES = {
    "北京": "101010100",
    "上海": "101020100",
    "深圳": "101280600",
    "杭州": "101210100",
    "广州": "101280100",
    "成都": "101270100",
    "南京": "101190100",
    "武汉": "101200100",
}


class BossSpider(BaseSpider):
    name = "boss"
    platform = "boss"

    custom_settings = {
        "ROBOTSTXT_OBEY": False,
        "DOWNLOAD_DELAY": 0,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cdp_port = int(os.environ.get("BOSS_CDP_PORT", "9222"))
        # 采集脚本路径
        self.crawler_script = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "boss_cdp_crawler.py",
        )
        self.logger.info(
            f"BOSS CDP 模式: 端口 {self.cdp_port}。"
            f"如未启动专用 Chrome，请先运行: python -m crawlers.setup_boss_chrome"
        )

    def start_requests(self):
        # 发一个占位 Request 到 CDP 端点，触发 parse 方法
        yield Request(
            f"http://127.0.0.1:{self.cdp_port}/json/version",
            callback=self.parse,
            meta={"tasks": self._build_tasks()},
            dont_filter=True,
            errback=self._on_error,
        )

    def _build_tasks(self):
        """构建所有 keyword+city 采集任务。"""
        tasks = []
        for keyword in self.keywords:
            for city in self.cities:
                city_code = BOSS_CITY_CODES.get(city, city)
                tasks.append({
                    "keyword": keyword,
                    "city": city,
                    "city_code": city_code,
                })
        return tasks

    async def parse(self, response: Response):
        """通过 subprocess 调用独立采集脚本，解析 JSONL 输出并 yield Item。"""
        tasks = response.meta.get("tasks") or self._build_tasks()
        if not tasks:
            self.logger.error("无采集任务，请通过 -a keywords= -a cities= 指定")
            return

        # Python 解释器路径（与当前进程相同）
        python_exe = sys.executable

        for task_idx, task in enumerate(tasks):
            keyword = task["keyword"]
            city = task["city"]
            city_code = task["city_code"]

            self.logger.info(f"开始采集: kw={keyword} city={city} ({city_code})")

            # 调用独立采集脚本
            cmd = [
                python_exe, self.crawler_script,
                "--keyword", keyword,
                "--city-code", city_code,
                "--cdp-port", str(self.cdp_port),
                "--max-pages", "5",
            ]

            try:
                # 用 subprocess.Popen 实时读取 stdout
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    cwd=os.path.dirname(self.crawler_script),
                )
            except Exception as e:
                self.logger.error(f"启动采集脚本失败: {e}")
                continue

            # 逐行读取 stdout（JSONL），实时 yield Item
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    item_data = json.loads(line)
                except json.JSONDecodeError as e:
                    self.logger.error(f"JSONL 解析失败: {e}, line={line[:100]}")
                    continue

                yield self.make_item(
                    source_id=item_data["source_id"],
                    source_url=item_data["source_url"],
                    title=item_data["title"],
                    company=item_data["company"],
                    location=item_data["location"],
                    salary=item_data["salary"],
                    experience=item_data["experience"],
                    education=item_data["education"],
                    tags=item_data["tags"],
                    description=item_data["description"],
                    requirements=item_data["requirements"],
                    raw_text=item_data["raw_text"],
                )

            # 等待进程结束，检查 stderr
            proc.wait()
            stderr_output = proc.stderr.read() if proc.stderr else ""
            if proc.returncode != 0:
                self.logger.error(
                    f"采集脚本退出码 {proc.returncode}, stderr: {stderr_output[-300:]}"
                )

            # 不同 keyword/city 之间不加延迟（脚本内部已有翻页延迟）

    def _on_error(self, failure):
        """请求失败回调。"""
        self.logger.error(
            f"占位请求失败: {failure.value}。"
            f"请确认专用 Chrome 已启动: python -m crawlers.setup_boss_chrome"
        )
