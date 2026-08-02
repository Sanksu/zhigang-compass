"""Glassdoor 爬虫（B 级 — 补充源，需走代理）。

策略（2026-07-29 重构）：
- 旧方案 JobSpy location 接口 400 + Playwright CSS 选择器失效
- 新方案：CDP 连接已启动的真实 Chrome/Edge（复用真实指纹 + 已绕过 Cloudflare）
- 直接从 SSR 页面 DOM 提取岗位（data-test 属性 + JSON-LD 双重兜底）
- 通过 subprocess 调用独立脚本，避免事件循环冲突

前置条件：
- 启动 CDP 浏览器：python -m crawlers.setup_boss_chrome
- 浏览器配置系统代理（Clash/V2Ray）访问 glassdoor
- 浏览器中先访问 glassdoor.com 完成一次 Cloudflare 验证

运行：
  scrapy crawl glassdoor -a keywords=Python -a cities="New York" -o output/glassdoor.jsonl
"""

import json
import os
import subprocess
import sys

from scrapy import Request
from scrapy.http import Response

from crawlers.base_spider import BaseSpider
from crawlers.settings import SUBPROCESS_TIMEOUT


CRAWLER_SCRIPT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "glassdoor_cdp_crawler.py")


class GlassdoorSpider(BaseSpider):
    name = "glassdoor"
    platform = "glassdoor"

    # Glassdoor 默认搜索美国城市
    cities = ["New York", "San Francisco", "Seattle", "Boston", "Remote"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 允许通过 -a max_pages=3 覆盖页数
        self.max_pages = int(kwargs.get("max_pages", "2"))

    def start_requests(self):
        """构建采集任务，用占位 Request 触发 parse。"""
        tasks = []
        for keyword in self.keywords:
            for city in self.cities:
                tasks.append({"keyword": keyword, "city": city})

        if not tasks:
            self.logger.error("无采集任务，请通过 -a keywords= -a cities= 指定")
            return

        cdp_url = os.environ.get("BOSS_CDP_URL", "http://127.0.0.1:9222")

        # 占位 Request 触发 parse（与 BOSS/maimai 一致：在 parse 中阻塞调用脚本，
        # 避免 start_requests 直接 yield Item 导致 feed exporter 写入已关闭文件）
        yield Request(
            f"{cdp_url}/json/version",
            callback=self.parse,
            meta={"tasks": tasks, "cdp_url": cdp_url},
            dont_filter=True,
            errback=self._on_error,
        )

    def parse(self, response: Response):
        """通过 subprocess 调用 CDP 采集脚本，解析 JSONL 输出并 yield Item。"""
        tasks = response.meta.get("tasks") or []
        cdp_url = response.meta.get("cdp_url", "http://127.0.0.1:9222")
        python_exe = sys.executable

        for task in tasks:
            keyword = task["keyword"]
            city = task["city"]
            self.logger.info(f"开始采集: kw={keyword} city={city}")

            cmd = [
                python_exe, CRAWLER_SCRIPT,
                "--keyword", keyword,
                "--city", city,
                "--max-pages", str(self.max_pages),
                "--cdp-url", cdp_url,
            ]

            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    cwd=os.path.dirname(CRAWLER_SCRIPT),
                    env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
                )
            except Exception as e:
                self.logger.error(f"启动 CDP 脚本失败: {e}")
                continue

            # 阻塞读取子进程输出（stdout/stderr 一并读取避免管道死锁），超时后终止
            try:
                stdout, stderr_output = proc.communicate(timeout=SUBPROCESS_TIMEOUT)
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout, stderr_output = proc.communicate()
                self.logger.error(f"CDP 脚本超时（>{SUBPROCESS_TIMEOUT}s），已终止")
                continue

            for line in stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    item_data = json.loads(line)
                except json.JSONDecodeError as e:
                    self.logger.error(f"JSONL 解析失败: {e}, line={line[:100]}")
                    continue

                yield self.make_item(
                    source_id=str(item_data.get("id", "")),
                    source_url=item_data.get("url", ""),
                    title=item_data.get("title", ""),
                    company=item_data.get("company", ""),
                    location=item_data.get("location", ""),
                    salary=item_data.get("salary", ""),
                    experience=item_data.get("experience_range", ""),
                    education=item_data.get("education", ""),
                    tags=self._extract_skills(item_data),
                    description=item_data.get("description", ""),
                    requirements="",
                    raw_text=json.dumps(item_data.get("raw", item_data), ensure_ascii=False),
                    post_date=item_data.get("date_posted", ""),
                )

            if proc.returncode != 0 and not (stderr_output and stderr_output.strip().endswith("count=0")):
                self.logger.warning(f"CDP 脚本退出码 {proc.returncode}")
            if stderr_output:
                for line in stderr_output.strip().splitlines()[-5:]:
                    self.logger.info(f"[cdp] {line}")

    def _on_error(self, failure):
        """占位请求失败回调。"""
        self.logger.error(
            f"占位请求失败: {failure.value}。"
            f"请确认专用 Chrome 已启动: python -m crawlers.setup_boss_chrome"
        )

    @staticmethod
    def _extract_skills(item_data: dict) -> list:
        """从描述中提取 Skills: 行。"""
        desc = item_data.get("description", "")
        skills = []
        for line in desc.split("\n"):
            line = line.strip()
            if line.lower().startswith("skills:"):
                skills_str = line.split(":", 1)[1].strip()
                skills = [s.strip() for s in skills_str.split(",") if s.strip()]
                break
        return skills
