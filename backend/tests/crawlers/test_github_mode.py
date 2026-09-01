"""GitHub 爬虫采集模式单元测试（08-16 用户决策：默认按热度全局采集）。

覆盖：
- 默认全局热度模式：不限语言，created 窗口内 sort=stars 取 top 100（单次请求）
- -a languages 覆盖：按语言分别取热度（每语言 20，合计 ≤100）
- 数据质量过滤（08-31）：刷票/作弊/破解垃圾仓库剔除 + README 正文补抓
"""

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_BACKEND))
sys.path.insert(0, str(_BACKEND / "data"))

from crawlers.spiders.github import (
    MIN_STARS,
    GithubSpider,
    _created_boundary,
    _is_spam_repo,
)


def _make_spider(**kwargs):
    spider = GithubSpider.__new__(GithubSpider)
    spider.name = "github"
    spider.platform = "github"
    spider.languages = list(kwargs.get("languages") or [])
    spider.since = kwargs.get("since") or "daily"
    spider.download_delay = 15
    spider.fetch_readme = kwargs.get("fetch_readme", True)
    spider._readme_fetched = 0
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

    def test_global_query_has_star_floor(self):
        """全局查询含 stars 下限，剔除 starbomb 刷票仓库。"""
        spider = _make_spider()
        req = list(spider.start_requests())[0]

        assert f"stars:>={MIN_STARS}" in self._query(req)

    def test_token_adds_auth_header(self, monkeypatch):
        """配置 GITHUB_TOKEN 时请求带 Bearer Authorization（提升限流配额）。"""
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test_123")
        spider = _make_spider()

        for req in spider.start_requests():
            assert req.headers.get("Authorization") == b"Bearer ghp_test_123"


class TestGithubQuality:
    """数据质量过滤 + README 正文补抓（08-31 修复）。"""

    @staticmethod
    def _query(req):
        from urllib.parse import unquote
        return unquote(req.url.split("?q=", 1)[1].split("&", 1)[0])

    @staticmethod
    def _json_resp(items: list, meta=None):
        import json
        from scrapy import Request
        from scrapy.http import HtmlResponse
        body = json.dumps({"items": items}).encode()
        req = Request(
            url="https://api.github.com/search/repositories",
            meta=meta or {"language": "", "since": "daily"},
            dont_filter=True,
        )
        return HtmlResponse(url=req.url, request=req, body=body, encoding="utf-8")

    def test_spam_repo_detection(self):
        """"作弊/破解/刷星营销"关键词判定。"""
        assert _is_spam_repo("x/CS2-Wallhack-2026", "Download CS2 wallhack free", [])
        assert _is_spam_repo("x/DataGrip-Crack", "Download DataGrip crack — free, working", [])
        assert _is_spam_repo("x/PUBG-PC-Radar-Hack", "Download hack radar", [])
        assert not _is_spam_repo("sussy/awesome-ml-tools", "A curated list of ML tools", ["ml"])
        assert not _is_spam_repo("sussy/dev-tool", "A CLI utility for developers", ["cli"])

    def test_api_to_item_drops_low_quality(self):
        """低质量仓库（作弊词/低 star/空语言/空描述）被丢弃。"""
        spider = _make_spider()
        cases_none = [
            {"full_name": "x/PUBG-Radar-Hack", "description": "Download hack", "language": "Python", "stargazers_count": 10},  # 作弊词
            {"full_name": "x/real", "description": "a real repo", "language": None, "stargazers_count": 100},  # 空语言
            {"full_name": "x/real", "description": "", "language": "Go", "stargazers_count": 100},  # 空描述
            {"full_name": "x/small", "description": "low star repo", "language": "Go", "stargazers_count": 5},  # 低 star
        ]
        for repo in cases_none:
            assert spider._api_to_item(repo, {"language": repo.get("language") or ""}) is None, repo

        good = {"full_name": "o/tool", "description": "a useful dev tool", "language": "Go", "stargazers_count": 200}
        item = spider._api_to_item(good, {"language": "Go"})
        assert item is not None
        assert item["source_id"] == "o/tool"
        assert item["description"] == "a useful dev tool"

    def test_parse_no_readme_yields_items_directly(self):
        """关闭补抓时 parse 直接产出元数据项（不含 readme）。"""
        spider = _make_spider(fetch_readme=False)
        repo = {"full_name": "o/tool", "description": "a useful dev tool", "language": "Go", "stargazers_count": 200}
        out = list(spider.parse(self._json_resp([repo])))
        assert len(out) == 1
        assert out[0]["source_id"] == "o/tool"
        assert "readme" not in out[0]

    def test_parse_fetch_readme_yields_requests(self):
        """开启补抓时 parse 产出 README 补抓 Request（而非直接产项）。"""
        spider = _make_spider(fetch_readme=True)
        from scrapy import Request
        repo = {"full_name": "o/tool", "description": "a useful dev tool", "language": "Go", "stargazers_count": 200}
        out = list(spider.parse(self._json_resp([repo])))
        assert len(out) == 1
        assert isinstance(out[0], Request)
        assert "/repos/o/tool/readme" in out[0].url

    def test_parse_readme_enrichment(self):
        """parse_readme 将 README 正文写入 item（含截断）。"""
        spider = _make_spider()
        from scrapy import Request
        from scrapy.http import HtmlResponse
        item = spider._api_to_item(
            {"full_name": "o/tool", "description": "a useful dev tool", "language": "Go", "stargazers_count": 200},
            {"language": "Go"},
        )
        readme_url = f"https://api.github.com/repos/{item['source_id']}/readme"
        req = Request(url=readme_url, meta={"item": item}, dont_filter=True)
        resp = HtmlResponse(url=readme_url, request=req, body=b"# Tool\nReal README body", encoding="utf-8")
        out = spider.parse_readme(resp)
        assert out["readme"].startswith("# Tool")
        assert item["readme"] == out["readme"]

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
        """daily 回看窗口为相对当前时刻的 36h（跨过 GitHub `created:` 索引滞后）。

        2026-09-01 掉量修复：原固定日历日期起点使上午早跑窗口 <28h 落空；
        现改为 UTC 滑动时间戳，任意触发时刻深度一致，且早于当前时刻。
        """
        import re
        from datetime import datetime, timezone

        from crawlers.spiders.github import _LOOKBACK_HOURS

        spider = _make_spider()
        req = list(spider.start_requests())[0]
        query = self._query(req)

        # 回看起点 = 当前 UTC 时刻 - daily 回看深度（36h）；查询含完整时间戳 + Z
        expect_start = datetime.now(timezone.utc).timestamp() - _LOOKBACK_HOURS["daily"] * 3600
        assert "created:>" in query
        m = re.search(r"created:>(\S+)", query)
        assert m and m.group(1).endswith("Z"), query  # 时间戳为 UTC（Z）且带时间成分
        ts = datetime.strptime(
            m.group(1), "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=timezone.utc).timestamp()
        # 允许 1 分钟时钟偏差：本应在当前时刻前 36h ±1min
        assert abs(expect_start - ts) < 60, (expect_start, ts)

    def test_created_boundary_is_utc_trailing(self):
        """_created_boundary 返回 UTC、相对传入回看深度的滑动时间戳。"""
        from datetime import datetime, timezone

        boundary = _created_boundary(36)
        # ISO-8601 Z 格式
        assert boundary.endswith("Z") and "T" in boundary
        parsed = datetime.strptime(boundary, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        # 早于当前 UTC 时刻约 36h
        seconds = (datetime.now(timezone.utc) - parsed).total_seconds()
        assert 35 * 3600 <= seconds <= 37 * 3600

    def test_no_token_no_auth_header(self, monkeypatch):
        """未配置 GITHUB_TOKEN 时请求不带 Authorization（维持匿名）。"""
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        spider = _make_spider()

        for req in spider.start_requests():
            assert req.headers.get("Authorization") is None

    def test_token_adds_auth_header(self, monkeypatch):
        """配置 GITHUB_TOKEN 时请求带 Bearer Authorization（提升限流配额）。"""
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test_123")
        spider = _make_spider()

        for req in spider.start_requests():
            assert req.headers.get("Authorization") == b"Bearer ghp_test_123"
