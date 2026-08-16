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
import time

from scrapy import Request
from scrapy.http import Response

from crawlers.base_spider import BaseSpider
from crawlers.settings import SUBPROCESS_TIMEOUT
from crawlers.setup_boss_chrome import ensure_cdp_chrome, platform_profile_dir


CRAWLER_SCRIPT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "glassdoor_cdp_crawler.py")


class GlassdoorSpider(BaseSpider):
    name = "glassdoor"
    platform = "glassdoor"

    # Glassdoor 默认搜索美国城市
    cities = ["New York", "San Francisco", "Seattle", "Boston", "Remote"]

    # 单次采集总上限（多关键词×城市任务合计，08-16 用户决策）
    max_items_total = 100

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 历史回爬（G-01）：-a history_days=90 放宽翻页上限（SSR 按发布倒序，
        # 由增量默认 2 页放宽到 50 页以覆盖更久时间窗）
        history_days = int(kwargs.get("history_days") or 0)
        default_pages = 50 if history_days else 2
        # 允许通过 -a max_pages=3 覆盖页数（非法输入回退默认，避免实例化崩溃）
        try:
            self.max_pages = int(kwargs.get("max_pages", str(default_pages)))
        except ValueError:
            self.max_pages = default_pages

    def start_requests(self):
        """构建采集任务，用占位 Request 触发 parse。"""
        # 空关键词/空城市 = 按平台热度/最新且不限位置采集（08-16 用户决策）
        keywords = self.keywords or [""]
        cities = self.cities or [""]
        tasks = []
        for keyword in keywords:
            for city in cities:
                tasks.append({"keyword": keyword, "city": city})

        if not tasks:
            self.logger.error("无采集任务，请通过 -a keywords= -a cities= 指定")
            return

        # Glassdoor 独立 CDP 浏览器：端口 9224 + 独立 profile（登录态/验证互不污染）
        cdp_url = os.environ.get("GLASSDOOR_CDP_URL", "http://127.0.0.1:9224")

        # 确保 CDP Chrome 可用（被环境回收时自动拉起），避免占位请求直接失败。
        # 启动时打开 glassdoor 首页，便于用户完成 Cloudflare 验证
        if not ensure_cdp_chrome(cdp_url, profile_dir=platform_profile_dir("glassdoor"),
                                 url="https://www.glassdoor.com/"):
            self.logger.error(f"CDP Chrome 启动失败（{cdp_url}），本次采集终止")
            return

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
        cdp_url = response.meta.get("cdp_url", "http://127.0.0.1:9224")
        python_exe = sys.executable

        task_total = len(tasks)
        _started = time.monotonic()
        _collected = 0  # 单次采集累计产出（跨关键词×城市任务合计，上限 max_items_total）
        for task_idx, task in enumerate(tasks):
            keyword = task["keyword"]
            city = task["city"]
            remaining = self.max_items_total - _collected
            if remaining <= 0:
                self.logger.info(
                    f"[glassdoor] 已达单次采集上限 {self.max_items_total} 条，"
                    f"跳过剩余 {task_total - task_idx} 个任务"
                )
                break
            self.logger.info(f"[glassdoor] 进度 {task_idx + 1}/{task_total}（已用 {time.monotonic() - _started:.0f}s）: 开始采集 kw={keyword} city={city}")

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
                self.logger.error(f"[glassdoor] 任务 {task_idx + 1}/{task_total} 超时（>{SUBPROCESS_TIMEOUT}s），已终止")
                continue

            item_count = 0
            for line in stdout.splitlines():
                if _collected >= self.max_items_total:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    item_data = json.loads(line)
                except json.JSONDecodeError as e:
                    self.logger.error(f"JSONL 解析失败: {e}, line={line[:100]}")
                    continue

                item_count += 1
                _collected += 1
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
            self.logger.info(f"[glassdoor] 进度 {task_idx + 1}/{task_total}: kw={keyword} city={city} 完成：产出 {item_count} 条（累计 {_collected}/{self.max_items_total}）")

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
