"""爬虫单次采集总上限单元测试（08-16 用户决策：单次采集 ≤ 100 条）。

覆盖四个可超量源（多关键词×城市/多分类/多标签任务合计）：
- JobSpy 基类（linkedin_public / indeed）：跨任务按剩余配额分配 --results-wanted
- arxiv：跨分类累计，达到上限 CloseSpider
- stackoverflow：跨标签/翻页累计，达到上限 CloseSpider
- glassdoor：跨任务累计，产出侧截断

未超量源（github 5×20=100 / zhilian max_results=100 / 课程源 ~90 / maimai 30）
不在本次修改范围，无对应测试。
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from tests.helpers import FakeProc
from scrapy.exceptions import CloseSpider
from scrapy.http import Request, Response, TextResponse, XmlResponse

_BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_BACKEND))
sys.path.insert(0, str(_BACKEND / "data"))

from crawlers.spiders.arxiv import ArxivSpider
from crawlers.spiders.glassdoor import GlassdoorSpider
from crawlers.spiders.linkedin_public import LinkedInPublicSpider
from crawlers.spiders.stackoverflow import StackoverflowSpider

TOTAL_CAP = 100


def _job_line(i: int, title: str = "Software Engineer") -> str:
    """构造一条 JobSpy/CDP 风格的 JSONL 产出行。

    默认标题用技术岗（08-18 LinkedIn 聚焦治理后：linkedin_public 产出前
    按技术关键词白名单过滤，非技术标题会被丢弃导致上限语义测试失真）。
    """
    return json.dumps(
        {"id": f"id-{i}", "title": f"{title} {i}", "company": "ACME",
         "job_url": f"https://example.com/job/{i}", "location": "New York",
         "salary_interval": "yearly", "min_amount": 100, "max_amount": 200,
         "currency": "USD", "job_type": "Full-time", "is_remote": False},
        ensure_ascii=False,
    )




def _fake_popen(lines_provider):
    """构造 fake subprocess.Popen：记录调用，并按 --results-wanted 截断产出。"""
    calls = []

    def fake_popen(cmd, **kwargs):
        calls.append(cmd)
        wanted = int(cmd[cmd.index("--results-wanted") + 1])
        return FakeProc(lines_provider(wanted))

    return fake_popen, calls


def _make_jobspy_spider(**kwargs):
    """构造不启动网络的 JobSpy 爬虫实例（__new__ 跳过 __init__）。"""
    spider = LinkedInPublicSpider.__new__(LinkedInPublicSpider)
    spider.name = "linkedin_public"
    spider.platform = "linkedin_public"
    spider.site_name = "linkedin"
    spider.crawler_script = "jobspy_crawler.py"
    spider.history_days = 0
    spider.results_wanted = int(kwargs.get("results_wanted") or 100)
    spider.max_items_total = int(kwargs.get("max_items_total") or TOTAL_CAP)
    spider.keywords = kwargs.get("keywords") or ["Python", "Java"]
    spider.cities = kwargs.get("cities") or ["New York", "Remote"]
    return spider


class TestJobSpyTotalCap:
    """linkedin_public / indeed 共用基类：跨关键词×城市任务合计 ≤ 100。"""

    def test_total_capped_across_tasks(self, monkeypatch):
        """4 个任务各 40 条产出 → 总产出恰为 100，剩余任务被跳过。"""
        fake_popen, calls = _fake_popen(lambda wanted: [_job_line(i) for i in range(min(40, wanted))])
        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        spider = _make_jobspy_spider()

        items = list(spider.start_requests())

        assert len(items) == TOTAL_CAP
        # 40 + 40 + 20（第 3 任务按剩余配额截断），第 4 任务不再启动
        assert len(calls) == 3
        # 第 3 个任务 --results-wanted 为剩余配额 20
        assert calls[2][calls[2].index("--results-wanted") + 1] == "20"

    def test_single_task_hits_cap_stops_later_tasks(self, monkeypatch):
        """首个任务产出即达上限 → 后续任务不再启动。"""
        fake_popen, calls = _fake_popen(lambda wanted: [_job_line(i) for i in range(wanted)])
        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        spider = _make_jobspy_spider()

        items = list(spider.start_requests())

        assert len(items) == TOTAL_CAP
        assert len(calls) == 1
        assert calls[0][calls[0].index("--results-wanted") + 1] == str(TOTAL_CAP)

    def test_results_wanted_below_cap_not_inflated(self, monkeypatch):
        """results_wanted < 上限时按各自上限执行，总量不受影响。"""
        fake_popen, calls = _fake_popen(lambda wanted: [_job_line(i) for i in range(min(30, wanted))])
        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        spider = _make_jobspy_spider(results_wanted=30)

        items = list(spider.start_requests())

        # 30 + 30 + 30 + 10 = 100
        assert len(items) == TOTAL_CAP
        assert len(calls) == 4


_ATOM_ENTRY = """\
<entry>
  <id>http://arxiv.org/abs/{aid}</id>
  <title>Test Paper {n}</title>
  <summary>Abstract of paper {n}</summary>
  <author><name>Author {n}</name></author>
  <published>2026-08-01T00:00:00Z</published>
  <category term="cs.AI"/>
  <link rel="related" type="application/pdf" href="http://arxiv.org/pdf/{aid}"/>
</entry>"""


def _atom_feed(count: int, start: int = 0) -> str:
    entries = "\n".join(
        _ATOM_ENTRY.format(aid=f"2401.{10000 + start + i:05d}", n=start + i)
        for i in range(count)
    )
    return f'<feed xmlns="http://www.w3.org/2005/Atom">{entries}</feed>'


def _make_arxiv_spider():
    spider = ArxivSpider.__new__(ArxivSpider)
    spider.name = "arxiv"
    spider.platform = "arxiv"
    spider.namespaces = {"atom": "http://www.w3.org/2005/Atom"}
    spider.max_items_total = TOTAL_CAP
    spider._collected = 0
    return spider


def _arxiv_response(body: str) -> XmlResponse:
    req = Request(url="http://export.arxiv.org/api/query", meta={"category": "cs.AI"})
    return XmlResponse(url="http://export.arxiv.org/api/query", body=body.encode("utf-8"), request=req)


class TestArxivTotalCap:
    """arxiv：多分类合计 ≤ 100，达到上限 CloseSpider。"""

    def test_cap_across_categories(self):
        spider = _make_arxiv_spider()

        items1 = list(spider.parse(_arxiv_response(_atom_feed(60))))
        assert len(items1) == 60
        assert spider._collected == 60

        with pytest.raises(CloseSpider) as exc_info:
            list(spider.parse(_arxiv_response(_atom_feed(60, start=60))))
        assert "100" in str(exc_info.value.reason)
        assert spider._collected == TOTAL_CAP


_STACKOVERFLOW_ITEM = {"question_id": "{qid}", "title": "Question {n}", "link": "https://stackoverflow.com/q/{qid}",
                       "creation_date": 1700000000, "tags": ["python"], "score": 1, "view_count": 1, "answer_count": 0}


def _make_stackoverflow_spider():
    spider = StackoverflowSpider.__new__(StackoverflowSpider)
    spider.name = "stackoverflow"
    spider.platform = "stackoverflow"
    spider.max_items_total = TOTAL_CAP
    spider._collected = 0
    spider.tags = ["python"]
    spider.max_pages = 1
    return spider


def _so_response(count: int, start: int = 0) -> TextResponse:
    items = [
        dict(_STACKOVERFLOW_ITEM, qid=1000 + start + i, n=start + i)
        for i in range(count)
    ]
    body = json.dumps({"items": items, "has_more": False}).encode("utf-8")
    req = Request(url="https://api.stackexchange.com/2.3/questions", meta={"tag": "python", "page": 1})
    return TextResponse(url="https://api.stackexchange.com/2.3/questions", body=body, request=req,
                        encoding="utf-8")


class TestStackoverflowTotalCap:
    """stackoverflow：多标签/翻页合计 ≤ 100，达到上限 CloseSpider。"""

    def test_cap_across_pages(self):
        spider = _make_stackoverflow_spider()

        items1 = list(spider.parse(_so_response(60)))
        assert len(items1) == 60
        assert spider._collected == 60

        with pytest.raises(CloseSpider) as exc_info:
            list(spider.parse(_so_response(60, start=60)))
        assert "100" in str(exc_info.value.reason)
        assert spider._collected == TOTAL_CAP


def _make_glassdoor_spider():
    spider = GlassdoorSpider.__new__(GlassdoorSpider)
    spider.name = "glassdoor"
    spider.platform = "glassdoor"
    spider.max_items_total = TOTAL_CAP
    spider.max_pages = 2
    return spider


def _glassdoor_response() -> Response:
    tasks = [{"keyword": "Python", "city": "New York"},
             {"keyword": "Python", "city": "Remote"},
             {"keyword": "Java", "city": "Remote"}]
    req = Request(url="http://127.0.0.1:9224/json/version",
                  meta={"tasks": tasks, "cdp_url": "http://127.0.0.1:9224"})
    return Response(url="http://127.0.0.1:9224/json/version", request=req)


class TestGlassdoorTotalCap:
    """glassdoor：跨关键词×城市任务合计 ≤ 100，产出侧截断。"""

    def test_total_capped_across_tasks(self, monkeypatch):
        def glassdoor_popen():
            calls = []

            def fake_popen(cmd, **kwargs):
                calls.append(cmd)
                return FakeProc([_job_line(i) for i in range(60)])

            return fake_popen, calls

        fake_popen, calls = glassdoor_popen()
        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        spider = _make_glassdoor_spider()

        items = list(spider.parse(_glassdoor_response()))

        assert len(items) == TOTAL_CAP
        # 60 + 40 截断，第 3 任务不启动
        assert len(calls) == 2
