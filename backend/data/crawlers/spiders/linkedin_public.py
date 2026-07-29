"""LinkedIn 爬虫（C 级 — 实验性，需走代理）。

策略（2026-07-29 重构）：
- 主链路：调用 JobSpy（speedyapply/JobSpy，2026-02 仍维护）采集
  - JobSpy 内部解析 LinkedIn JSON-LD 结构化数据，字段丰富稳定
  - JobSpy 是同步阻塞库，与 Scrapy Twisted 冲突 → 用 subprocess 调独立脚本
- 参考项目：https://github.com/speedyapply/JobSpy

⚠️ 合规提醒：
- 仅采集公开搜索页
- 需走 Clash/V2Ray 代理（HTTPS_PROXY 环境变量）
- LinkedIn 公开页在中国大陆 IP 无法访问，必须走代理

运行：
  # 先启动 Clash（7890 端口）
  $env:HTTPS_PROXY="http://127.0.0.1:7890"
  scrapy crawl linkedin_public -a keywords=Python -a cities="New York" -o output/linkedin_public.jsonl
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from scrapy.http import Response

from crawlers.base_spider import BaseSpider

# 独立采集脚本路径
CRAWLER_SCRIPT = str(Path(__file__).resolve().parent.parent / "linkedin_jobspy_crawler.py")


class LinkedInPublicSpider(BaseSpider):
    name = "linkedin_public"
    platform = "linkedin_public"

    # LinkedIn 默认搜索美国城市
    cities = ["New York", "San Francisco", "Seattle", "Boston", "Remote"]

    # 单次采集岗位数上限
    results_wanted = 20

    def start_requests(self):
        """通过 subprocess 调用 JobSpy 采集脚本，解析 JSONL 输出并 yield Item。"""
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
                "--results-wanted", str(self.results_wanted),
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
                self.logger.error(f"启动 JobSpy 脚本失败: {e}")
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

                salary = self._format_salary(
                    item_data.get("interval"),
                    item_data.get("min_amount"),
                    item_data.get("max_amount"),
                    item_data.get("currency", "USD"),
                )

                yield self.make_item(
                    source_id=str(item_data.get("id", "")),
                    source_url=item_data.get("job_url", ""),
                    title=item_data.get("title", ""),
                    company=str(item_data.get("company", "")),
                    location=item_data.get("location", ""),
                    salary=salary,
                    experience=item_data.get("experience_range", "") or item_data.get("job_level", ""),
                    education="",
                    tags=self._build_tags(item_data),
                    description=item_data.get("description", ""),
                    requirements="",
                    raw_text=json.dumps(item_data, ensure_ascii=False),
                )

            # 检查 stderr
            stderr = proc.stderr.read() if proc.stderr else ""
            proc.wait()
            if proc.returncode != 0:
                self.logger.error(f"JobSpy 脚本退出码 {proc.returncode}: {stderr[-500:]}")
            elif stderr:
                self.logger.debug(f"JobSpy stderr: {stderr[-300:]}")

    def parse(self, response: Response):
        """占位：start_requests 已直接 yield Item，无需 parse。"""
        return

    @staticmethod
    def _format_salary(interval, min_amount, max_amount, currency) -> str:
        """格式化 JobSpy 薪资字段为可读字符串。"""
        if not interval or not min_amount:
            return ""
        unit = {"yearly": "年", "monthly": "月", "weekly": "周", "hourly": "时"}.get(interval, interval)
        cur = "USD" if currency == "USD" else currency or ""
        if max_amount and max_amount != min_amount:
            return f"{cur} {min_amount}-{max_amount}/{unit}"
        return f"{cur} {min_amount}/{unit}"

    @staticmethod
    def _build_tags(item_data: dict) -> list[str]:
        """从 JobSpy 字段构建标签列表。"""
        tags = []
        if item_data.get("job_type"):
            tags.append(str(item_data["job_type"]))
        if item_data.get("is_remote"):
            tags.append("Remote")
        if item_data.get("job_level"):
            tags.append(str(item_data["job_level"]))
        if item_data.get("skills"):
            skills = item_data["skills"]
            if isinstance(skills, str):
                tags.extend(skills.split(","))
            elif isinstance(skills, list):
                tags.extend([str(s) for s in skills])
        return [t.strip() for t in tags if t and t.strip() and t.strip() != "nan"]
