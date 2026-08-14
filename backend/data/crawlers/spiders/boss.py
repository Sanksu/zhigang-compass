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
import time

from scrapy import Request
from scrapy.http import Response

from crawlers.base_spider import BaseSpider
from crawlers.settings import SUBPROCESS_TIMEOUT
from crawlers.setup_boss_chrome import ensure_cdp_chrome, platform_profile_dir

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

# 增量采集翻页上限（默认）；历史回爬放宽（G-01，由 --since-days 时间截断提前停页）
INCREMENTAL_MAX_PAGES = 5
BACKFILL_MAX_PAGES = 50


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
        # 历史回爬（G-01）：-a history_days=90 放宽翻页上限并透传 --since-days，
        # 由 boss_cdp_crawler 按发布时间截断；未指定时保持默认增量采集
        self.history_days = int(kwargs.get("history_days") or 0)
        # 允许 -a max_pages 覆盖页数上限（crawl_platform 统一透传；非法输入回退默认）
        try:
            self._max_pages = int(kwargs.get("max_pages") or 0) or None
        except ValueError:
            self._max_pages = None
        # CDP 调试端点：本地默认 http://127.0.0.1:9222，支持局域网内容器浏览器
        self.cdp_url = os.environ.get("BOSS_CDP_URL", "http://127.0.0.1:9222")
        # cookies 文件模式：容器等无 CDP 浏览器环境复用导出的登录态文件
        self.cookies_file = os.environ.get("BOSS_COOKIES_FILE")
        # 采集脚本路径
        self.crawler_script = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "boss_cdp_crawler.py",
        )
        if self.cookies_file:
            self.logger.info(
                f"BOSS cookies 文件模式: {self.cookies_file}。"
                f"登录态从文件读取，CDP 仅作占位请求靶子。"
                f"导出命令: python boss_cdp_crawler.py --export-cookies {self.cookies_file}"
            )
        else:
            self.logger.info(
                f"BOSS CDP 模式: {self.cdp_url}。"
                f"如未启动专用 Chrome，请先运行: python -m crawlers.setup_boss_chrome"
            )

    def _build_cmd(self, task: dict) -> list[str]:
        """构造 boss_cdp_crawler 采集命令（含历史回爬参数）。"""
        max_pages = self._max_pages or (BACKFILL_MAX_PAGES if self.history_days else INCREMENTAL_MAX_PAGES)
        cmd = [
            sys.executable, self.crawler_script,
            "--keyword", task["keyword"],
            "--city-code", task["city_code"],
            "--cdp-url", self.cdp_url,
            "--max-pages", str(max_pages),
        ]
        if self.history_days:
            cmd.extend(["--since-days", str(self.history_days)])
        if self.cookies_file:
            cmd.extend(["--cookies-file", self.cookies_file])
        return cmd

    def start_requests(self):
        # cookies 文件模式无需 CDP 浏览器（容器无真实 Chrome，登录态从文件读）：
        # 占位请求仍发向 CDP 端点触发 parse——端点不可达属预期，由 errback 转发到 parse。
        if not self.cookies_file:
            # 确保 CDP Chrome 可用（被环境回收时自动拉起），避免占位请求直接失败。
            # boss 独立 profile（9222 + boss-chrome-profile）；主窗口保持 about:blank——
            # zhipin 反爬会检测 CDP 自动化并关闭 zhipin 页面，若主窗口是 zhipin 会导致
            # Chrome 整个退出；用户需手动在浏览器中打开 zhipin.com 完成登录（手动操作不被风控）
            if not ensure_cdp_chrome(self.cdp_url, profile_dir=platform_profile_dir("boss")):
                self.logger.error(f"CDP Chrome 启动失败（{self.cdp_url}），本次采集终止")
                return
        # 发一个占位 Request 到 CDP 端点，触发 parse 方法
        yield Request(
            f"{self.cdp_url}/json/version",
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
                city_code = BOSS_CITY_CODES.get(city)
                if not city_code:
                    self.logger.warning(f"跳过未映射城市: {city}（BOSS 城市码表仅含 {list(BOSS_CITY_CODES)}）")
                    continue
                tasks.append({
                    "keyword": keyword,
                    "city": city,
                    "city_code": city_code,
                })
        return tasks

    def parse(self, response: Response):
        """通过 subprocess 调用独立采集脚本，解析 JSONL 输出并 yield Item。"""
        tasks = response.meta.get("tasks") or self._build_tasks()
        if not tasks:
            self.logger.error("无采集任务，请通过 -a keywords= -a cities= 指定")
            return

        # Python 解释器路径（与当前进程相同，_build_cmd 内部使用）
        task_total = len(tasks)
        _started = time.monotonic()
        for task_idx, task in enumerate(tasks):
            keyword = task["keyword"]
            city = task["city"]
            city_code = task["city_code"]

            self.logger.info(f"[boss] 进度 {task_idx + 1}/{task_total}（已用 {time.monotonic() - _started:.0f}s）: 开始采集 kw={keyword} city={city} ({city_code})")

            # 调用独立采集脚本
            cmd = self._build_cmd(task)

            try:
                # 用 subprocess.Popen 实时读取 stdout
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    cwd=os.path.dirname(self.crawler_script),
                    env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
                )
            except Exception as e:
                self.logger.error(f"启动采集脚本失败: {e}")
                continue

            # 阻塞读取子进程输出（stdout/stderr 一并读取避免管道死锁），超时后终止
            try:
                stdout, stderr_output = proc.communicate(timeout=SUBPROCESS_TIMEOUT)
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout, stderr_output = proc.communicate()
                self.logger.error(f"[boss] 任务 {task_idx + 1}/{task_total} 超时（>{SUBPROCESS_TIMEOUT}s），已终止")
                continue

            item_count = 0
            for line in stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    item_data = json.loads(line)
                except json.JSONDecodeError as e:
                    self.logger.error(f"JSONL 解析失败: {e}, line={line[:100]}")
                    continue

                item_count += 1
                yield self.make_item(
                    source_id=item_data.get("source_id", ""),
                    source_url=item_data.get("source_url", ""),
                    title=item_data.get("title", ""),
                    company=item_data.get("company", ""),
                    location=item_data.get("location", ""),
                    salary=item_data.get("salary", ""),
                    experience=item_data.get("experience", ""),
                    education=item_data.get("education", ""),
                    tags=item_data.get("tags") or [],
                    description=item_data.get("description", ""),
                    requirements=item_data.get("requirements", ""),
                    raw_text=item_data.get("raw_text", ""),
                    post_date=item_data.get("post_date", ""),
                )

            if proc.returncode != 0:
                self.logger.error(
                    f"采集脚本退出码 {proc.returncode}, stderr: {stderr_output[-300:]}"
                )
            elif stderr_output:
                # 转发 CDP 脚本关键日志（连接/cookies/导航/API 请求），便于实时排查。
                # 跳过脚本自身的"采集完成"汇总（下方输出产出统计，避免重复反馈）
                for line in stderr_output.strip().splitlines()[-20:]:
                    if "采集完成" in line:
                        continue
                    self.logger.info(f"[cdp] {line}")
            self.logger.info(f"[boss] 进度 {task_idx + 1}/{task_total}: kw={keyword} city={city} 完成：产出 {item_count} 条")

            # 不同 keyword/city 之间不加延迟（脚本内部已有翻页延迟）

    def _on_error(self, failure):
        """请求失败回调。"""
        if self.cookies_file:
            # cookies 模式：CDP 端点不可达属预期（不依赖浏览器），转发到 parse 继续采集。
            # parse 是生成器，必须 yield from 才能把产出的 Item 流转给引擎。
            # parse 仅依赖 response.meta（= request.meta），传 request 即可。
            yield from self.parse(failure.request)
            return
        self.logger.error(
            f"占位请求失败: {failure.value}。"
            f"请确认专用 Chrome 已启动: python -m crawlers.setup_boss_chrome"
        )
