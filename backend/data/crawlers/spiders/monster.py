"""Monster 爬虫（A 级 — 稳定源，需 CDP 浏览器）。

策略（2026-07-29 重构）：
- 主链路：CDP 连接已启动的 Chrome/Edge，拦截 Monster 内部 API XHR
  - Monster 用 DataDome 反爬，直连 API 被拦截（403），headless 也被检测
  - CDP 复用真实浏览器指纹 + 用户手动完成 DataDome challenge
  - 拦截 appsapi.monster.io 的 search-jobs 接口，直接拿 JSON
- subprocess 调独立脚本（避免 asyncio 与 Twisted 冲突）
- 参考项目：https://github.com/shahidirfan100/Monster-Job-Scraper（2026-07-28 更新）

⚠️ 合规提醒：
- 仅采集公开搜索页
- 需先启动带 CDP 的 Chrome/Edge（复用 BOSS 的 setup_boss_chrome.py）

运行：
  # 1. 先启动带 CDP 的 Chrome/Edge
  python -m crawlers.setup_boss_chrome
  # 2. 在浏览器中访问 monster.com 完成 DataDome challenge
  # 3. 运行爬虫
  scrapy crawl monster -a keywords=Python -a cities="New York" -o output/monster.jsonl
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from scrapy import Request
from scrapy.http import Response

from crawlers.base_spider import BaseSpider
from crawlers.settings import SUBPROCESS_TIMEOUT
from crawlers.setup_boss_chrome import ensure_cdp_chrome, platform_profile_dir

# 独立采集脚本路径
CRAWLER_SCRIPT = str(Path(__file__).resolve().parent.parent / "monster_cdp_crawler.py")


class MonsterSpider(BaseSpider):
    name = "monster"
    platform = "monster"

    # Monster 默认搜索美国城市
    cities = ["New York", "San Francisco", "Seattle", "Boston", "Remote"]

    # 单次最大页数
    max_pages = 2

    def start_requests(self):
        """构建采集任务，用占位 Request 触发 parse。"""
        tasks = []
        for keyword in self.keywords:
            for city in self.cities:
                tasks.append({"keyword": keyword, "city": city})

        if not tasks:
            self.logger.error("无采集任务，请通过 -a keywords= -a cities= 指定")
            return

        # Monster 独立 CDP 浏览器：端口 9223 + 独立 profile（登录态/验证互不污染）
        cdp_url = os.environ.get("MONSTER_CDP_URL", "http://127.0.0.1:9223")

        # 确保 CDP Chrome 可用（被环境回收时自动拉起），避免占位请求直接失败。
        # 启动时打开 monster 首页，便于用户完成 DataDome 验证
        import time as _time
        t0 = _time.monotonic()
        self.logger.info(f"[monster] 检查 CDP Chrome（{cdp_url}）...")
        if ensure_cdp_chrome(cdp_url, profile_dir=platform_profile_dir("monster"),
                             url="https://www.monster.com/"):
            self.logger.info(f"[monster] CDP Chrome 就绪（耗时 {_time.monotonic() - t0:.1f}s）")
        else:
            self.logger.error(f"[monster] CDP Chrome 启动失败（{cdp_url}），本次采集终止")
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
        """通过 subprocess 调用采集脚本，解析 JSONL 输出并 yield Item。"""
        tasks = response.meta.get("tasks") or []
        cdp_url = response.meta.get("cdp_url", "http://127.0.0.1:9223")
        python_exe = sys.executable

        task_total = len(tasks)
        _started = time.monotonic()
        for task_idx, task in enumerate(tasks):
            keyword = task["keyword"]
            city = task["city"]
            self.logger.info(f"[monster] 进度 {task_idx + 1}/{task_total}（已用 {time.monotonic() - _started:.0f}s）: 开始采集 kw={keyword} city={city}（调用 CDP 脚本）")

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
                self.logger.error(f"启动采集脚本失败: {e}")
                continue

            # 阻塞读取子进程输出（stdout/stderr 一并读取避免管道死锁），超时后终止
            try:
                stdout, stderr = proc.communicate(timeout=SUBPROCESS_TIMEOUT)
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout, stderr = proc.communicate()
                self.logger.error(f"[monster] 任务 {task_idx + 1}/{task_total} 超时（>{SUBPROCESS_TIMEOUT}s），已终止")
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
                    source_id=str(item_data.get("id", "")),
                    source_url=item_data.get("url", ""),
                    title=item_data.get("title", ""),
                    company=str(item_data.get("company", "")),
                    location=item_data.get("location", ""),
                    salary=item_data.get("salary", ""),
                    experience=item_data.get("experience_range", ""),
                    education=item_data.get("education", ""),
                    tags=self._build_tags(item_data),
                    post_date=item_data.get("date_posted", ""),
                    description=item_data.get("description", ""),
                    requirements="",
                    raw_text=json.dumps(item_data, ensure_ascii=False),
                )

            if proc.returncode != 0:
                self.logger.error(f"[monster] CDP 脚本退出码 {proc.returncode}: {stderr[-500:]}")
            elif stderr:
                # 转发 CDP 脚本关键日志（连接/cookies/导航/拦截），便于实时排查。
                # 跳过脚本自身的"采集完成"汇总（下方输出产出统计，避免重复反馈）
                for line in stderr.strip().splitlines()[-20:]:
                    if "采集完成" in line:
                        continue
                    self.logger.info(f"[cdp] {line}")
            self.logger.info(f"[monster] 进度 {task_idx + 1}/{task_total}: kw={keyword} city={city} 完成：产出 {item_count} 条")

    def _on_error(self, failure):
        """占位请求失败回调。"""
        self.logger.error(
            f"占位请求失败: {failure.value}。"
            f"请确认专用 Chrome 已启动: python -m crawlers.setup_boss_chrome"
        )

    @staticmethod
    def _build_tags(item_data: dict) -> list[str]:
        tags = []
        if item_data.get("job_type"):
            tags.append(str(item_data["job_type"]))
        if item_data.get("is_remote"):
            tags.append("Remote")
        skills = item_data.get("skills", [])
        if isinstance(skills, list):
            tags.extend([str(s) for s in skills])
        elif isinstance(skills, str):
            tags.extend(skills.split(","))
        return [t.strip() for t in tags if t and t.strip() and t.strip() != "nan"]
