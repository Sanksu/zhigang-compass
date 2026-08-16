"""GitHub 爬虫采集模式单元测试（08-16 用户决策：默认按热度全局采集）。

覆盖：
- 默认全局热度模式：不限语言，created 窗口内 sort=stars 取 top 100（单次请求）
- -a languages 覆盖：按语言分别取热度（每语言 20，合计 ≤100）
"""

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_BACKEND))
sys.path.insert(0, str(_BACKEND / "data"))

from crawlers.spiders.github import GithubSpider


def _make_spider(**kwargs):
    spider = GithubSpider.__new__(GithubSpider)
    spider.name = "github"
    spider.platform = "github"
    spider.languages = list(kwargs.get("languages") or [])
    spider.since = kwargs.get("since") or "daily"
    spider.download_delay = 15
    return spider


class TestGithubMode:
    @staticmethod
    def _query(req):
        from urllib.parse import unquote
        return unquote(req.url.split("?q=", 1)[1].split("&", 1)[0])

    def test_default_global_hot(self):
        """默认无语言过滤：单请求、created 窗口、sort=stars、per_page=100。"""
        spider = _make_spider()

        requests = list(spider.start_requests())

        assert len(requests) == 1
        url = requests[0].url
        assert "language:" not in self._query(requests[0])
        assert "created:>" in self._query(requests[0])
        assert "sort=stars" in url
        assert "per_page=100" in url

    def test_languages_override_per_language(self):
        """传 -a languages= 时按语言分别采集（每语言 20）。"""
        spider = _make_spider(languages=["python", "go"])

        requests = list(spider.start_requests())

        assert len(requests) == 2
        for req in requests:
            assert "language:" in self._query(req)
            assert "per_page=20" in req.url
            assert "sort=stars" in req.url

    def test_since_window_mapping(self):
        """since 窗口映射到 created 参数（daily=1 天）。"""
        spider = _make_spider()
        req = list(spider.start_requests())[0]

        assert "created:>20" in self._query(req)
