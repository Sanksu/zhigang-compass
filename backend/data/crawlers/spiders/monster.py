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
from pathlib import Path

from scrapy.http import Response

from crawlers.base_spider import BaseSpider

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
        """通过 subprocess 调用采集脚本，解析 JSONL 输出并 yield Item。"""
        tasks = []
        for keyword in self.keywords:
            for city in self.cities:
                tasks.append({"keyword": keyword, "city": city})

        if not tasks:
            self.logger.error("无采集任务，请通过 -a keywords= -a cities= 指定")
            return

        python_exe = sys.executable
        cdp_port = os.environ.get("BOSS_CDP_PORT", "9222")

        for task in tasks:
            keyword = task["keyword"]
            city = task["city"]
            self.logger.info(f"开始采集: kw={keyword} city={city}")

            cmd = [
                python_exe, CRAWLER_SCRIPT,
                "--keyword", keyword,
                "--city", city,
                "--max-pages", str(self.max_pages),
                "--cdp-port", cdp_port,
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
                    source_id=str(item_data.get("id", "")),
                    source_url=item_data.get("url", ""),
                    title=item_data.get("title", ""),
                    company=str(item_data.get("company", "")),
                    location=item_data.get("location", ""),
                    salary=item_data.get("salary", ""),
                    experience=item_data.get("experience_range", ""),
                    education="",
                    tags=self._build_tags(item_data),
                    description=item_data.get("description", ""),
                    requirements="",
                    raw_text=json.dumps(item_data, ensure_ascii=False),
                )

            stderr = proc.stderr.read() if proc.stderr else ""
            proc.wait()
            if proc.returncode != 0:
                self.logger.error(f"采集脚本退出码 {proc.returncode}: {stderr[-500:]}")
            elif stderr:
                self.logger.debug(f"采集 stderr: {stderr[-300:]}")

    def parse(self, response: Response):
        return

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
