"""JobSpy 采集基类（Indeed / LinkedIn 共用）。

两者仅 site_name 与默认城市不同，其余逻辑（subprocess 调用、JSONL 解析、
薪资格式化、标签构建）完全一致，抽公共基类避免重复。

子类需设置：
- name / platform
- site_name（JobSpy site_name 参数，如 "indeed" / "linkedin"）
- cities 默认城市列表
"""

import json
import os
import sys
import time
from pathlib import Path

from scrapy.http import Response

from crawlers.base_spider import BaseSpider, iter_jsonl, run_script
from crawlers.settings import CRAWL_ITEMS_CAP


class JobSpyBaseSpider(BaseSpider):
    """通过 subprocess 调用 jobspy_crawler.py 采集 JobSpy 支持的平台。"""

    site_name: str = ""  # 子类必须设置：indeed / linkedin
    results_wanted = 20  # 单任务（关键词×城市）岗位数上限
    max_items_total = CRAWL_ITEMS_CAP  # 单次采集总上限（跨任务合计，08-16 可后台配置）

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.crawler_script = str(
            Path(__file__).resolve().parent.parent / "jobspy_crawler.py"
        )
        # 历史回爬（G-01）：-a history_days=90 透传 --days-old 到 jobspy_crawler
        self.history_days = int(kwargs.get("history_days") or 0)

    def _build_cmd(self, keyword: str, city: str, limit: int | None = None) -> list[str]:
        """构造 jobspy_crawler 采集命令（含历史回爬 --days-old 参数）。

        limit: 单次采集剩余配额（max_items_total 减去已产出），None 不额外限制。
        """
        wanted = min(self.results_wanted, limit) if limit else self.results_wanted
        cmd = [
            sys.executable, self.crawler_script,
            "--site", self.site_name,
            "--keyword", keyword,
            "--city", city,
            "--results-wanted", str(wanted),
        ]
        if self.history_days:
            cmd.extend(["--days-old", str(self.history_days)])
        return cmd

    def start_requests(self):
        """通过 subprocess 调用 JobSpy 采集脚本，解析 JSONL 输出并 yield Item。"""
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

        task_total = len(tasks)
        _started = time.monotonic()
        _collected = 0  # 单次采集累计产出（跨关键词×城市任务合计，上限 max_items_total）
        for task_idx, task in enumerate(tasks):
            keyword = task["keyword"]
            city = task["city"]
            remaining = self.max_items_total - _collected
            if remaining <= 0:
                self.logger.info(
                    f"[{self.platform}] 已达单次采集上限 {self.max_items_total} 条，"
                    f"跳过剩余 {task_total - task_idx} 个任务"
                )
                break
            self.logger.info(f"[{self.platform}] 进度 {task_idx + 1}/{task_total}（已用 {time.monotonic() - _started:.0f}s）: 开始采集 kw={keyword or '(全局)'} city={city}")

            cmd = self._build_cmd(keyword, city, remaining)

            result = run_script(cmd, os.path.dirname(self.crawler_script), self.logger,
                               f"[{self.platform}] 任务 {task_idx + 1}/{task_total}")
            if result is None:
                continue
            stdout, stderr, returncode = result

            item_count = 0
            for item_data in iter_jsonl(stdout, self.logger):
                item_count += 1
                salary = self._format_salary(
                    item_data.get("salary_interval"),
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
                    post_date=item_data.get("date_posted", ""),
                    description=item_data.get("description", ""),
                    requirements="",
                    raw_text=json.dumps(item_data, ensure_ascii=False),
                )

            if returncode != 0:
                self.logger.error(f"JobSpy 脚本退出码 {returncode}: {stderr[-500:]}")
            elif stderr:
                self.logger.debug(f"JobSpy stderr: {stderr[-300:]}")
            _collected += item_count
            self.logger.info(f"[{self.platform}] 进度 {task_idx + 1}/{task_total}: kw={keyword} city={city} 完成：产出 {item_count} 条（累计 {_collected}/{self.max_items_total}）")

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
