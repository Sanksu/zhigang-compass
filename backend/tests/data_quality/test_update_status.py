"""数据更新新鲜度单元测试（DA-M4-03，设计文档 T+1 承诺）。"""

from datetime import datetime, timedelta, timezone


from app.services.data_quality.update_status import parse_crawled_at, platform_freshness

_TZ_CN = timezone(timedelta(hours=8))
_NOW = datetime(2026, 8, 2, 12, 0, tzinfo=_TZ_CN)


class TestParseCrawledAt:
    def test_iso_with_offset(self):
        dt = parse_crawled_at("2026-08-02T02:37:24.108906+08:00")
        assert dt is not None
        assert dt.tzinfo is not None

    def test_utc_z_suffix(self):
        dt = parse_crawled_at("2026-08-01T03:23:40.258142Z")
        assert dt is not None
        assert dt.tzinfo is not None

    def test_naive_treated_as_utc(self):
        dt = parse_crawled_at("2026-08-01T03:23:40")
        assert dt is not None
        assert dt.tzinfo == timezone.utc

    def test_invalid_returns_none(self):
        assert parse_crawled_at("not-a-date") is None
        assert parse_crawled_at(None) is None
        assert parse_crawled_at("") is None


class TestPlatformFreshness:
    def test_fresh_platform_within_t1(self):
        rows = [
            {"source": "boss", "crawled_at": "2026-08-02T02:00:00+08:00"},
            {"source": "zhilian", "crawled_at": "2026-08-02T02:37:24+08:00"},
        ]
        r = platform_freshness(rows, now=_NOW)
        assert r["t1_compliant"] is True
        assert r["stale_sources"] == []
        assert all(p["fresh"] for p in r["platforms"])

    def test_stale_platform_beyond_t1(self):
        rows = [{"source": "boss", "crawled_at": "2026-07-20T03:00:00+00:00"}]
        r = platform_freshness(rows, now=_NOW)
        assert r["t1_compliant"] is False
        assert r["stale_sources"] == ["boss"]
        assert r["platforms"][0]["fresh"] is False
        assert r["platforms"][0]["days_since"] > 1.0

    def test_unparsable_crawled_at_counts_stale(self):
        """无法解析的抓取时间不静默放行，计为不新鲜。"""
        rows = [{"source": "boss", "crawled_at": "bad-format"}]
        r = platform_freshness(rows, now=_NOW)
        assert r["t1_compliant"] is False
        assert r["platforms"][0]["fresh"] is False
        assert r["platforms"][0]["last_crawl"] is None

    def test_takes_latest_per_source(self):
        rows = [
            {"source": "boss", "crawled_at": "2026-07-01T00:00:00+00:00"},
            {"source": "boss", "crawled_at": "2026-08-02T00:00:00+00:00"},
        ]
        r = platform_freshness(rows, now=_NOW)
        assert r["platforms"][0]["last_crawl"].startswith("2026-08-02")
        assert r["platforms"][0]["fresh"] is True

    def test_empty_returns_compliant(self):
        r = platform_freshness([], now=_NOW)
        assert r["t1_compliant"] is True
        assert r["platforms"] == []
