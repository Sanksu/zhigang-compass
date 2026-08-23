# -*- coding: utf-8 -*-
"""Tool 分类回退词表 + 存量回填单测（08-24 盘点 P2）。"""

from app.services.kg.kg_service import effective_tool_category
from scripts.backfill_tool_categories import build_updates


class TestEffectiveToolCategory:
    def test_llm_category_takes_priority(self):
        assert effective_tool_category("框架", "GitHub") == "框架"

    def test_fallback_to_whitelist_mapping(self):
        assert effective_tool_category("", "GitHub") == "工程协作"
        assert effective_tool_category("  ", "Python") == "编程语言"

    def test_miss_returns_sentinel(self):
        assert effective_tool_category(None, "某不知名内部平台") == "未分类"

    def test_llm_blank_string_stripped(self):
        assert effective_tool_category(" 云服务 ", "X") == "云服务"


class TestBuildUpdates:
    def test_hit_and_sentinel_partition(self):
        rows = build_updates(["GitHub", "内部平台"], {"GitHub": "工程协作"})
        by_name = {r["name"]: r["category"] for r in rows}
        assert by_name == {"GitHub": "工程协作", "内部平台": "未分类"}

    def test_blank_names_skipped(self):
        assert build_updates(["", "  ", None], {}) == []

    def test_empty_input(self):
        assert build_updates([], {"GitHub": "工程协作"}) == []
