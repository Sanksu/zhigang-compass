"""爬虫空城市行为单元测试（08-16 用户决策：无默认城市，空城市 = 不限位置）。

覆盖：
- JobSpy 基类（linkedin/indeed）：空城市 → keyword 任务 city 传空串（不限位置）
- glassdoor：同上
- zhilian：空城市 → URL 不带 jl 参数（全国）
"""

import json
import subprocess
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_BACKEND))
sys.path.insert(0, str(_BACKEND / "data"))

from crawlers.spiders.glassdoor import GlassdoorSpider
from crawlers.spiders.linkedin_public import LinkedInPublicSpider
from crawlers.spiders.zhilian import ZhilianSpider


class _FakeProc:
    def __init__(self):
        self.returncode = 0

    def communicate(self, timeout=None):
        return ("", "")


class TestJobSpyEmptyCity:
    """linkedin/indeed：空城市 = 不限位置（city 传空串给 jobspy）。"""

    def test_empty_city_passes_empty_location(self, monkeypatch):
        calls = []

        def fake_popen(cmd, **kwargs):
            calls.append(cmd)
            return _FakeProc()

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        spider = LinkedInPublicSpider.__new__(LinkedInPublicSpider)
        spider.name = "linkedin_public"
        spider.platform = "linkedin_public"
        spider.site_name = "linkedin"
        spider.crawler_script = "jobspy_crawler.py"
        spider.history_days = 0
        spider.results_wanted = 100
        spider.max_items_total = 100
        spider.keywords = ["Python"]
        spider.cities = []

        list(spider.start_requests())

        assert len(calls) == 1
        assert calls[0][calls[0].index("--city") + 1] == ""


class TestGlassdoorEmptyCity:
    def test_empty_city_passes_empty_location(self, monkeypatch):
        calls = []

        def fake_popen(cmd, **kwargs):
            calls.append(cmd)
            return _FakeProc()

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        spider = GlassdoorSpider.__new__(GlassdoorSpider)
        spider.name = "glassdoor"
        spider.platform = "glassdoor"
        spider.max_items_total = 100
        spider.max_pages = 2
        spider.keywords = ["Python"]
        spider.cities = []

        tasks = [{"keyword": "Python", "city": ""}]
        req = __import__("scrapy.http", fromlist=["Request"]).Request(
            url="http://127.0.0.1:9224/json/version",
            meta={"tasks": tasks, "cdp_url": "http://127.0.0.1:9224"},
        )
        resp = __import__("scrapy.http", fromlist=["Response"]).Response(
            url="http://127.0.0.1:9224/json/version", request=req,
        )
        list(spider.parse(resp))

        assert len(calls) == 1
        assert calls[0][calls[0].index("--city") + 1] == ""


class TestZhilianEmptyCity:
    def test_empty_city_url_without_jl(self):
        spider = ZhilianSpider.__new__(ZhilianSpider)
        spider.name = "zhilian"
        spider.platform = "zhilian"
        spider.keywords = ["Python"]
        spider.cities = []
        spider.history_days = 0
        spider._max_pages = 5
        spider._max_results = 100
        spider._items_collected = 0

        requests = list(spider.start_requests())

        assert len(requests) == 1
        # 空城市：URL 不带 jl 参数（全国招聘）
        assert "jl=" not in requests[0].url
        assert "kw=Python" in requests[0].url
