"""爬取 trigger 端点测试（BE-M4-05）。

端点依赖 admin RBAC + DB + ARQ 队列，测试覆盖纯逻辑部分：
- 平台白名单（PLATFORM_META）与 spider 映射完整性
- 映射目标确实存在对应 Scrapy spider 文件（防平台漂移）
- 历史行组装（_history_row）：spider/keyword 来源与中文名映射
"""

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from app.api.v1.admin import PLATFORM_META, _PLATFORM_TO_SPIDER, _history_row

_SPIDERS_DIR = Path(__file__).resolve().parents[2] / "data" / "crawlers" / "spiders"


def test_platform_mapping_covers_all_platform_meta():
    """每个 PLATFORM_META 平台 ID 都必须可映射到 spider。"""
    assert set(_PLATFORM_TO_SPIDER) >= set(PLATFORM_META)


def test_linkedin_maps_to_linkedin_public():
    """前端平台 ID linkedin → Scrapy spider linkedin_public。"""
    assert _PLATFORM_TO_SPIDER["linkedin"] == "linkedin_public"


def test_all_mapped_spiders_exist():
    """映射的每个 spider 名都对应 crawlers/spiders 下的真实文件。"""
    for spider in _PLATFORM_TO_SPIDER.values():
        assert (_SPIDERS_DIR / f"{spider}.py").exists(), f"spider 缺失: {spider}"


def test_platform_meta_has_no_removed_source():
    """拉勾网已移除（设计文档 R-14），不应出现在平台白名单。"""
    assert "lagou" not in PLATFORM_META


class TestHistoryRow:
    def test_spider_keyword_and_items_from_result(self):
        """crawl_platform 合并后的 result 完整映射到历史行。"""
        task = SimpleNamespace(
            id="t1",
            status="success",
            error="",
            created_at=datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc),
            result={
                "platform": "boss",
                "keyword": "高级前端",
                "spider": "boss",
                "output_file": "output/boss_xxx.jsonl",
                "items": 5,
            },
        )
        row = _history_row(task)
        assert row["platform"] == "boss"
        assert row["platform_name"] == "BOSS直聘"
        assert row["keyword"] == "高级前端"
        assert row["items"] == 5
        assert row["status"] == "success"
        assert row["created_at"].startswith("2026-08-02")

    def test_fallback_to_platform_and_defaults(self):
        """旧版任务（无 spider）回退触发时 platform；缺失字段给默认值。"""
        task = SimpleNamespace(
            id="t2",
            status="pending",
            error="",
            created_at=None,
            result={"platform": "zhilian", "keyword": "Java"},
        )
        row = _history_row(task)
        assert row["platform"] == "zhilian"
        assert row["platform_name"] == "智联招聘"
        assert row["keyword"] == "Java"
        assert row["items"] == 0
        assert row["created_at"] is None

    def test_unknown_spider_falls_back_to_raw_name(self):
        """未知 spider 名直接回显，keyword/error 取默认与真实值。"""
        task = SimpleNamespace(
            id="t3",
            status="failed",
            error="爬虫退出码 1",
            created_at=None,
            result={"spider": "nonexistent_spider"},
        )
        row = _history_row(task)
        assert row["platform_name"] == "nonexistent_spider"
        assert row["keyword"] == ""
        assert row["error"] == "爬虫退出码 1"
        assert row["status"] == "failed"
