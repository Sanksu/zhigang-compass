# -*- coding: utf-8 -*-
"""岗位职能域（岗位投影 Leiden）单元测试。

覆盖 load_position_projection 的边过滤/权重/降级、merge_singletons 的
单点合并与代表岗命名确定性、guard_domain_distribution 的退化门禁。
"""

import pytest

from app.services.graph_algorithms.network import load_position_projection
from scripts.sync_position_domains import (
    GENERAL_DOMAIN_ID,
    GENERAL_DOMAIN_NAME,
    guard_domain_distribution,
    merge_singletons,
)


class _StubSession:
    """最小 Neo4j 会话桩：run() 返回预置记录列表。"""

    def __init__(self, rows=None, error: Exception | None = None):
        self._rows = rows or []
        self._error = error

    def run(self, query, **params):
        if self._error:
            raise self._error
        return list(self._rows)


class TestLoadPositionProjection:
    def test_shared_below_threshold_filtered(self):
        rows = [
            {"aid": "p1", "aname": "前端", "bid": "p2", "bname": "全栈", "shared": 5, "w": 4.2},
            {"aid": "p1", "aname": "前端", "bid": "p3", "bname": "精算", "shared": 1, "w": 0.3},
        ]
        graph, name_map = load_position_projection(_StubSession(rows))
        assert set(graph) == {"p1", "p2"}
        assert "p3" not in graph and "p3" not in name_map
        assert graph["p1"]["p2"] == pytest.approx(4.2)

    def test_symmetric_registration_and_names(self):
        rows = [{"aid": "a", "aname": "投资分析师", "bid": "b", "bname": "信贷分析师", "shared": 3, "w": 1.9}]
        graph, name_map = load_position_projection(_StubSession(rows))
        assert graph["a"]["b"] == graph["b"]["a"] == pytest.approx(1.9)
        assert name_map == {"a": "投资分析师", "b": "信贷分析师"}

    def test_neo4j_unreachable_degrades_to_empty(self):
        graph, name_map = load_position_projection(_StubSession(error=RuntimeError("down")))
        assert graph == {} and name_map == {}


class TestMergeSingletons:
    def test_singleton_cluster_merges_to_general(self):
        membership = {"p1": 0, "p2": 0, "p3": 1}  # p3 单点
        name_map = {"p1": "前端开发工程师", "p2": "Vue前端开发工程师", "p3": "Python开发工程师"}
        freq = {"p1": 203, "p2": 5, "p3": 162}
        assign = merge_singletons(membership, name_map, freq)
        assert assign["p3"] == (GENERAL_DOMAIN_ID, GENERAL_DOMAIN_NAME)
        # 多岗域：代表岗 = 域内最高 freq
        assert assign["p1"] == assign["p2"] == ("dom_0", "前端开发工程师")

    def test_freq_tie_breaks_by_name(self):
        membership = {"p1": 0, "p2": 0}
        name_map = {"p1": "b岗", "p2": "a岗"}
        assign = merge_singletons(membership, name_map, {"p1": 3, "p2": 3})
        assert assign["p1"][1] == "a岗"

    def test_name_missing_falls_back_to_id(self):
        assign = merge_singletons({"p1": 0, "p2": 0}, {}, {"p1": 1})
        assert assign["p1"][1] == "p1"


class TestGuardDomainDistribution:
    def test_single_domain_swallows_all_rejected(self):
        assign = {f"p{i}": ("dom_0", "前端") for i in range(10)}
        with pytest.raises(ValueError, match="单簇吞并"):
            guard_domain_distribution(assign)

    def test_too_few_semantic_domains_rejected(self):
        # 低占比（2/8）但语义域仅 2 个——绕过单簇吞并门禁，命中语义域数门禁
        assign = {}
        for d in range(2):
            for i in range(3):
                assign[f"p{d}_{i}"] = (f"dom_{d}", f"域{d}")
        assign["g1"] = (GENERAL_DOMAIN_ID, GENERAL_DOMAIN_NAME)
        assign["g2"] = (GENERAL_DOMAIN_ID, GENERAL_DOMAIN_NAME)
        with pytest.raises(ValueError, match="语义域数"):
            guard_domain_distribution(assign)

    def test_healthy_distribution_passes(self):
        assign = {}
        for d in range(6):
            for i in range(4):
                assign[f"p{d}_{i}"] = (f"dom_{d}", f"域{d}")
        assign["px"] = (GENERAL_DOMAIN_ID, GENERAL_DOMAIN_NAME)
        stats = guard_domain_distribution(assign)
        assert stats["semantic_domains"] == 6
        assert stats["max_domain_ratio"] == pytest.approx(4 / 25)


# ---- 08-24 补强：LLM 语义域名 sanitize + 孤立岗兜底口径 ----

from scripts.sync_position_domains import (  # noqa: E402
    _DomainNameItem,
    sanitize_llm_names,
)


class TestSanitizeLlmNames:
    def test_valid_names_pass_through(self):
        items = [_DomainNameItem(cluster="数据分析师", name="金融数据分析")]
        out = sanitize_llm_names(items, {"数据分析师"}, {"数据分析师": {"数据分析师", "精算师"}})
        assert out == {"数据分析师": "金融数据分析"}

    def test_unknown_cluster_key_dropped(self):
        items = [_DomainNameItem(cluster="不存在的簇", name="前端开发")]
        assert sanitize_llm_names(items, {"数据分析师"}, {}) == {}

    def test_duplicate_name_second_falls_back(self):
        items = [
            _DomainNameItem(cluster="A", name="算法研发"),
            _DomainNameItem(cluster="B", name="算法研发"),
        ]
        out = sanitize_llm_names(items, {"A", "B"}, {"A": set(), "B": set()})
        assert out == {"A": "算法研发"}

    def test_name_equal_to_member_position_rejected(self):
        items = [_DomainNameItem(cluster="前端开发工程师", name="前端开发工程师")]
        out = sanitize_llm_names(
            items, {"前端开发工程师"}, {"前端开发工程师": {"前端开发工程师", "Web前端"}},
        )
        assert out == {}

    def test_whitespace_and_empty_handled(self):
        items = [
            _DomainNameItem(cluster=" A ", name="  测试 "),
            _DomainNameItem(cluster="B", name="  "),
        ]
        out = sanitize_llm_names(items, {"A", "B"}, {"A": set(), "B": set()})
        assert out == {"A": "测试"}


class TestDomainDecisionRecords:
    """PR4b：域名决策落 llm_decision_records（cluster_label shadow）。"""

    def test_records_persisted_as_shadow(self, monkeypatch):
        from types import SimpleNamespace

        from scripts import sync_position_domains as script

        persisted: list = []

        async def _fake_persist(record):
            persisted.append(record)
            return "rec"

        monkeypatch.setattr("app.services.llm_decision.persist_record", _fake_persist)
        llm = SimpleNamespace(_providers=[{"name": "deepseek", "model": "m"}])
        script._try_persist_domain_records(
            {"数据分析师": "金融数据分析", "算法工程师": "机器视觉算法"},
            {"数据分析师": ["数据分析师", "精算师"], "算法工程师": ["算法工程师"]},
            llm,
        )
        assert len(persisted) == 2
        by_key = {r.entity_id: r for r in persisted}
        record = by_key["数据分析师"]
        assert record.domain == "cluster_label"
        assert record.status == "shadow"
        assert record.risk_tier == "R0"
        assert record.gate_result == "pass"
        assert record.structured_output == {"cluster": "数据分析师", "name": "金融数据分析"}
        assert record.evidence_refs == [{"member_count": 2}]
        assert record.provider == "deepseek"
        assert by_key["算法工程师"].structured_output["name"] == "机器视觉算法"

    def test_persist_failure_does_not_raise(self, monkeypatch):
        from scripts import sync_position_domains as script

        async def _boom(record):
            raise RuntimeError("pg down")

        monkeypatch.setattr("app.services.llm_decision.persist_record", _boom)
        script._try_persist_domain_records(
            {"数据分析师": "金融数据分析"}, {"数据分析师": ["数据分析师"]}, object(),
        )  # 不抛异常即通过
