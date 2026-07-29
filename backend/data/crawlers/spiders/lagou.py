"""拉勾网爬虫（B 级 — 补充源）。

策略（2026-07-29 重构）：
- 旧方案：Playwright 直接渲染被阿里云 WAF 拦截（滑动验证页面）
- 新方案：CDP 连接已启动的真实 Chrome/Edge（复用真实指纹 + 已通过 WAF + 已登录）
- 在页面上下文中用 page.evaluate(fetch) 调用内部 API positionAjax.json
- Cookie 自动带上（包括 X-Anit-Token），绕过 WAF
- 通过 subprocess 调用独立脚本，避免事件循环冲突

前置条件（重要！）：
1. 启动 CDP 浏览器：python -m crawlers.setup_boss_chrome
2. 在浏览器中访问 https://www.lagou.com/jobs/list_Python?city=北京
3. 完成滑动验证（阿里云 WAF）
4. 登录账号（扫码/手机号）
5. 保持浏览器开启，爬虫通过 CDP 复用登录态

运行：
  scrapy crawl lagou -a keywords=Python -a cities=北京 -o output/lagou.jsonl
"""

import json
import os
import subprocess
import sys

from crawlers.base_spider import BaseSpider


CRAWLER_SCRIPT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "lagou_cdp_crawler.py")


class LagouSpider(BaseSpider):
    name = "lagou"
    platform = "lagou"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 允许通过 -a max_pages=3 覆盖页数
        self.max_pages = int(kwargs.get("max_pages", "3"))

    def start_requests(self):
        """通过 subprocess 调用 CDP 采集脚本，解析 JSONL 输出并 yield Item。"""
        tasks = []
        for keyword in self.keywords:
            for city in self.cities:
                tasks.append({"keyword": keyword, "city": city})

        if not tasks:
            self.logger.error("无采集任务，请通过 -a keywords= -a cities= 指定")
            return

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
            ]

            # 传递 CDP 端口（与 BOSS/Monster 共用）
            cdp_port = os.environ.get("BOSS_CDP_PORT", "9222")
            cmd.extend(["--cdp-port", cdp_port])

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
                    source_id=str(item_data.get("source_id", "")),
                    source_url=item_data.get("source_url", ""),
                    title=item_data.get("title", ""),
                    company=item_data.get("company", ""),
                    location=item_data.get("location", ""),
                    salary=item_data.get("salary", ""),
                    experience=item_data.get("experience", ""),
                    education=item_data.get("education", ""),
                    tags=item_data.get("tags", []),
                    description=item_data.get("description", ""),
                    requirements=item_data.get("requirements", ""),
                    raw_text=item_data.get("raw_text", ""),
                    job_type=item_data.get("job_type", ""),
                )

            # 等待进程结束，检查错误
            proc.wait()
            stderr_output = proc.stderr.read() if proc.stderr else ""
            if proc.returncode != 0:
                self.logger.warning(f"CDP 脚本退出码 {proc.returncode}")
            if stderr_output:
                for line in stderr_output.strip().splitlines()[-5:]:
                    self.logger.info(f"[cdp] {line}")
