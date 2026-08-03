"""脉脉爬虫（C 级 — 实验性，合规限制）。

合规措施（S2+S3，project_memory 强制约束）：
- 注明用于竞赛演示不商用（X-Collection-Purpose 头）
- 数据脱敏（CleaningPipeline 自动 PII 清洗：手机号/邮箱/身份证）
- 限频 ≤100 req/h（settings.RATE_LIMIT.maimai = 5 req/min）
- 夜间运行 22:00-08:00（start_requests 时间守卫强制）

策略（2026-07-29 重构）：
- 旧方案：maimai.cn/job/search 是专栏页，无岗位数据；maimai.cn 上无公开职位搜索页
- 新发现：脉脉职位实际托管在飞书招聘系统 maimai.jobs.feishu.cn
- 飞书招聘页 SSR 渲染 10 个岗位卡片，无需登录态即可采集
- 通过 CDP 连接已启动的真实 Chrome/Edge，从 DOM 提取 a[href*="/position/"] 卡片
- 通过 subprocess 调用独立脚本，避免事件循环冲突

注意：脉脉.jobs.feishu.cn 是脉脉公司自己的招聘页（招聘脉脉员工），
非脉脉平台全量职位。作为 C 级实验性源的样本数据足够。

前置条件：
- 启动 CDP 浏览器：python -m crawlers.setup_boss_chrome
- 飞书招聘页无需登录态，直连即可访问

运行（仅夜间 22:00-08:00）：
  scrapy crawl maimai -a keywords=Python -o output/maimai.jsonl
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

from scrapy import Request
from scrapy.exceptions import CloseSpider
from scrapy.http import Response

from crawlers.base_spider import BaseSpider
from crawlers.settings import MAIMAI_COMPLIANCE, SUBPROCESS_TIMEOUT
from crawlers.setup_boss_chrome import ensure_cdp_chrome, platform_profile_dir


CRAWLER_SCRIPT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "maimai_cdp_crawler.py")


# 合规时间窗口（22:00-08:00）
NIGHT_START_HOUR = MAIMAI_COMPLIANCE["schedule_hours"][0]  # 22
NIGHT_END_HOUR = MAIMAI_COMPLIANCE["schedule_hours"][1]    # 8


class MaimaiSpider(BaseSpider):
    name = "maimai"
    platform = "maimai"

    # 脉脉飞书招聘页无城市筛选，固定城市列表仅作日志记录
    cities = ["北京"]

    def start_requests(self):
        """合规守卫：仅夜间 22:00-08:00 启动。

        飞书招聘页无关键字搜索功能，keywords 仅作日志记录，
        只需调用一次 CDP 采集脚本即可拿到所有岗位。
        """
        current_hour = datetime.now(timezone(timedelta(hours=8))).hour
        in_night_window = current_hour >= NIGHT_START_HOUR or current_hour < NIGHT_END_HOUR
        if not in_night_window:
            raise CloseSpider(
                f"合规拒绝：脉脉仅允许在 {NIGHT_START_HOUR}:00-{NIGHT_END_HOUR}:00 夜间运行，"
                f"当前小时 {current_hour}。请夜间再执行 scrapy crawl maimai"
            )

        # 脉脉独立 CDP 浏览器：端口 9225 + 独立 profile（登录态/验证互不污染）
        cdp_url = os.environ.get("MAIMAI_CDP_URL", "http://127.0.0.1:9225")
        keyword = self.keywords[0] if self.keywords else ""

        self.logger.info(f"开始采集脉脉飞书招聘页（合规声明：{MAIMAI_COMPLIANCE['annotation']}）")

        # 确保 CDP Chrome 可用（被环境回收时自动拉起），避免占位请求直接失败。
        # 启动时打开脉脉飞书招聘页（无需登录态），便于用户直观看到采集页面
        if not ensure_cdp_chrome(cdp_url, profile_dir=platform_profile_dir("maimai"),
                                 url="https://maimai.jobs.feishu.cn/index"):
            self.logger.error(f"CDP Chrome 启动失败（{cdp_url}），本次采集终止")
            return

        # 占位 Request 触发 parse（与 BOSS 一致：在 parse 中阻塞调用 CDP 脚本，
        # 避免 start_requests 直接 yield Item 导致 feed exporter 写入已关闭文件）
        yield Request(
            f"{cdp_url}/json/version",
            callback=self.parse,
            meta={"keyword": keyword, "cdp_url": cdp_url},
            dont_filter=True,
            errback=self._on_error,
        )

    def parse(self, response: Response):
        """通过 subprocess 调用 CDP 采集脚本，解析 JSONL 输出并 yield Item。"""
        keyword = response.meta.get("keyword", "")
        cdp_url = response.meta.get("cdp_url", "http://127.0.0.1:9225")

        cmd = [sys.executable, CRAWLER_SCRIPT, "--keyword", keyword, "--cdp-url", cdp_url]

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
            return

        # 阻塞读取子进程输出（stdout/stderr 一并读取避免管道死锁），超时后终止
        try:
            stdout, stderr_output = proc.communicate(timeout=SUBPROCESS_TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr_output = proc.communicate()
            self.logger.error(f"CDP 脚本超时（>{SUBPROCESS_TIMEOUT}s），已终止")
            return

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
                company=item_data.get("company", "脉脉"),
                location=item_data.get("location", ""),
                salary=item_data.get("salary", ""),
                experience=item_data.get("experience_range", ""),
                education="",
                tags=[t for t in (item_data.get("category", ""), item_data.get("job_type", "")) if t],
                description=item_data.get("description", ""),
                requirements="",
                raw_text=json.dumps(item_data.get("raw", item_data), ensure_ascii=False),
            )

        if proc.returncode != 0:
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
