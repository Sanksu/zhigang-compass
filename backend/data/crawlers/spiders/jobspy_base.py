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
import subprocess
import sys
from pathlib import Path

from scrapy.http import Response

from crawlers.base_spider import BaseSpider
from crawlers.settings import SUBPROCESS_TIMEOUT


class JobSpyBaseSpider(BaseSpider):
    """通过 subprocess 调用 jobspy_crawler.py 采集 JobSpy 支持的平台。"""

    site_name: str = ""  # 子类必须设置：indeed / linkedin
    results_wanted = 20  # 单次采集岗位数上限

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.crawler_script = str(
            Path(__file__).resolve().parent.parent / "jobspy_crawler.py"
        )

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
                python_exe, self.crawler_script,
                "--site", self.site_name,
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
                    cwd=os.path.dirname(self.crawler_script),
                    env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
                )
            except Exception as e:
                self.logger.error(f"启动 JobSpy 脚本失败: {e}")
                continue

            # 阻塞读取子进程输出（stdout/stderr 一并读取避免管道死锁），超时后终止
            try:
                stdout, stderr = proc.communicate(timeout=SUBPROCESS_TIMEOUT)
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout, stderr = proc.communicate()
                self.logger.error(f"JobSpy 脚本超时（>{SUBPROCESS_TIMEOUT}s），已终止")
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
