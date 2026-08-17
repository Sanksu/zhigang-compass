"""爬虫空关键词行为单元测试（08-16 用户决策：爬虫不再内置默认关键词）。

覆盖（空关键词 → 平台热度/最新模式）：
- JobSpy 基类（linkedin/indeed）：空关键词 → 每城市默认岗位流，不再报"无采集任务"
- zhilian：空关键词 → kw='' 平台默认推荐列表
- arxiv：空分类 → 全局最新（无 search_query）
- stackoverflow：空标签 → 全局热度（sort=votes，无 tagged）
- coursera/edx：空关键词 → 浏览页（无 query/q 参数）
- icourse163：空关键词 → 单次默认课程流请求（--keyword 传空串）
"""

import asyncio
import json
import subprocess
import sys
from pathlib import Path

from scrapy.http import Request, Response
from tests.helpers import FakeProc

_BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_BACKEND))
sys.path.insert(0, str(_BACKEND / "data"))

from crawlers.spiders.arxiv import ArxivSpider
from crawlers.spiders.coursera import CourseraSpider
from crawlers.spiders.edx import EdxSpider
from crawlers.spiders.icourse163 import Icourse163Spider
from crawlers.spiders.linkedin_public import LinkedInPublicSpider
from crawlers.spiders.stackoverflow import StackoverflowSpider
from crawlers.spiders.zhilian import ZhilianSpider


def _job_line(i: int) -> str:
    return json.dumps(
        {"id": f"id-{i}", "title": f"Job {i}", "company": "ACME",
         "job_url": f"https://example.com/job/{i}", "location": "New York",
         "salary_interval": "yearly", "min_amount": 100, "max_amount": 200,
         "currency": "USD"},
        ensure_ascii=False,
    )




class TestJobSpyEmptyKeywords:
    """linkedin/indeed：空关键词 = 每城市默认岗位流，总上限 100 不变。"""

    def test_empty_keywords_per_city_tasks(self, monkeypatch):
        calls = []

        def fake_popen(cmd, **kwargs):
            calls.append(cmd)
            wanted = int(cmd[cmd.index("--results-wanted") + 1])
            return FakeProc([_job_line(i) for i in range(min(60, wanted))])

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        spider = LinkedInPublicSpider.__new__(LinkedInPublicSpider)
        spider.name = "linkedin_public"
        spider.platform = "linkedin_public"
        spider.site_name = "linkedin"
        spider.crawler_script = "jobspy_crawler.py"
        spider.history_days = 0
        spider.results_wanted = 100
        spider.max_items_total = 100
        spider.keywords = []
        spider.cities = ["New York", "Remote"]

        items = list(spider.start_requests())

        # 2 城市 × 空关键词：60 + 40 = 100，无"无采集任务"报错
        assert len(items) == 100
        assert len(calls) == 2
        # 空关键词以空串传给 jobspy 脚本
        assert calls[0][calls[0].index("--keyword") + 1] == ""


class TestZhilianEmptyKeywords:
    def test_empty_keywords_default_feed(self):
        spider = ZhilianSpider.__new__(ZhilianSpider)
        spider.name = "zhilian"
        spider.platform = "zhilian"
        spider.keywords = []
        spider.cities = ["北京"]
        spider.history_days = 0
        spider._max_pages = 5
        spider._max_results = 100
        spider._items_collected = 0

        requests = list(spider.start_requests())

        assert len(requests) == 1
        assert "kw=" in requests[0].url
        assert "kw=&pn=1" in requests[0].url
        assert requests[0].meta["keyword"] == ""


class TestArxivEmptyCategories:
    def test_empty_categories_global_feed(self):
        spider = ArxivSpider.__new__(ArxivSpider)
        spider.name = "arxiv"
        spider.platform = "arxiv"
        spider.namespaces = {"atom": "http://www.w3.org/2005/Atom"}
        spider.max_items_total = 100
        spider.max_results = 100
        spider._collected = 0
        spider.categories = []

        requests = list(spider.start_requests())

        assert len(requests) == 1
        # cat:* 通配全部分类（API 不接受省略 search_query，实测 400）
        assert "search_query=cat%3A%2A" in requests[0].url
        assert "sortBy=submittedDate" in requests[0].url
        assert requests[0].meta["category"] == "global"


class TestStackoverflowEmptyTags:
    def test_empty_tags_global_hot(self):
        spider = StackoverflowSpider.__new__(StackoverflowSpider)
        spider.name = "stackoverflow"
        spider.platform = "stackoverflow"
        spider.max_items_total = 100
        spider._collected = 0
        spider.tags = []
        spider.max_pages = 1

        requests = list(spider.start_requests())

        assert len(requests) == 1
        assert "sort=votes" in requests[0].url
        assert "tagged=" not in requests[0].url


class TestCourseEmptyKeywords:
    def test_coursera_empty_keywords_browse(self):
        spider = CourseraSpider.__new__(CourseraSpider)
        spider.name = "coursera"
        spider.platform = "coursera"
        spider.keywords = []
        spider.max_pages = 3
        spider.download_delay = 15

        requests = list(spider.start_requests())

        assert len(requests) == 1
        assert requests[0].url == "https://www.coursera.org/browse"
        assert "query=" not in requests[0].url

    def test_edx_empty_keywords_browse(self):
        spider = EdxSpider.__new__(EdxSpider)
        spider.name = "edx"
        spider.platform = "edx"
        spider.keywords = []
        spider.max_pages = 3
        spider.download_delay = 15

        requests = list(spider.start_requests())

        assert len(requests) == 1
        assert requests[0].url == "https://www.edx.org/search"
        assert "q=" not in requests[0].url


class TestIcourse163EmptyKeywords:
    def test_empty_keywords_default_course_stream(self, monkeypatch):
        calls = []

        def fake_popen(cmd, **kwargs):
            calls.append(cmd)
            return FakeProc([])

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        spider = Icourse163Spider.__new__(Icourse163Spider)
        spider.name = "icourse163"
        spider.platform = "icourse163"
        spider.keywords = []
        spider.max_pages = 3
        spider.crawler_script = "icourse163_crawler.py"
        spider.download_delay = 10

        req = Request(url="https://www.icourse163.org/search.htm", meta={"keywords": []})
        resp = Response(url="https://www.icourse163.org/search.htm", request=req)

        async def _run():
            async for _ in spider.parse(resp):
                pass

        asyncio.run(_run())

        # 空关键词不再报"无采集关键词"，单次默认课程流请求（--keyword 传空串）
        assert len(calls) == 1
        assert calls[0][calls[0].index("--keyword") + 1] == ""
