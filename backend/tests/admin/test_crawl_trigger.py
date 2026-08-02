"""爬取 trigger 端点测试（BE-M4-05）。

端点依赖 admin RBAC + DB + ARQ 队列，测试覆盖纯逻辑部分：
- 平台白名单（PLATFORM_META）与 spider 映射完整性
- 映射目标确实存在对应 Scrapy spider 文件（防平台漂移）
"""

from pathlib import Path

from app.api.v1.admin import PLATFORM_META, _PLATFORM_TO_SPIDER

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
