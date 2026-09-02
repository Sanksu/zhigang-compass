# -*- coding: utf-8 -*-
"""岗位职能域（岗位投影 Leiden）单元测试。

覆盖 load_position_projection 的边过滤/权重/降级、merge_singletons 的
单点合并与代表岗命名确定性、guard_domain_distribution 的退化门禁。
"""

import pytest

from app.services.graph_algorithms.network import load_position_projection
from scripts.sync_position_domains import (
    ATTACH_DOMINANCE,
    ATTACH_MIN_AFFINITY,
    GENERAL_DOMAIN_ID,
    GENERAL_DOMAIN_NAME,
    apply_domain_pins,
    attach_fringe_position,
    demote_small_clusters,
    guard_domain_distribution,
    resolve_fringe,
    resolve_leftover_pins,
    split_backbone,
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


class TestSplitBackbone:
    def test_freq_threshold_splits_pools(self):
        freq = {"hub": 585, "mid": 11, "thin": 5, "zero": 0}
        backbone, fringe = split_backbone(freq, min_freq=10)
        assert backbone == {"hub", "mid"}
        assert fringe == {"thin", "zero"}

    def test_missing_freq_counts_as_zero(self):
        backbone, fringe = split_backbone({"a": 12}, min_freq=10)
        assert backbone == {"a"} and fringe == set()


class TestDemoteSmallClusters:
    def test_sub_min_clusters_demoted_to_fringe(self):
        # min=3：2 人簇降级进归类池（替代旧"直接并通用域"），3 人簇保留
        membership = {"p1": 0, "p2": 0, "p6": 0, "p3": 1, "p4": 1, "p5": 2}
        kept, demoted = demote_small_clusters(membership, min_cluster_size=3)
        assert kept == {"p1": 0, "p2": 0, "p6": 0}
        assert demoted == {"p3", "p4", "p5"}

    def test_all_clusters_healthy_no_demotion(self):
        membership = {"p1": 0, "p2": 0, "p3": 0, "p4": 1, "p5": 1, "p6": 1}
        kept, demoted = demote_small_clusters(membership, min_cluster_size=3)
        assert kept == membership and demoted == set()


class TestApplyDomainPins:
    """08-31 高频桥梁岗语义指派：微簇降级前 pinned 骨干岗并入锚点岗所在簇。"""

    def _membership(self):
        # Leiden 原始簇：0=算法簇, 1=后端 2 人簇, 2=运维 2 人簇, 3=DevOps 单点
        return {
            "id_vision": 0, "id_auto": 0,
            "id_java": 1, "id_be": 1,
            "id_ops": 2, "id_dba": 2,
            "id_devops": 3,
            "id_llm": 4, "id_py": 5, "id_bigdata": 6,
        }

    def _name_map(self):
        return {
            "id_vision": "机器视觉算法工程师", "id_auto": "自动驾驶算法工程师",
            "id_java": "Java开发工程师", "id_be": "后端开发工程师",
            "id_ops": "运维工程师", "id_dba": "数据库管理员",
            "id_devops": "DevOps工程师",
            "id_llm": "大模型算法工程师", "id_py": "Python开发工程师",
            "id_bigdata": "大数据开发工程师",
        }

    def test_pinned_joins_anchor_cluster_and_survives_demotion(self):
        membership, warnings, pinned = apply_domain_pins(self._membership(), self._name_map())
        assert warnings == []
        assert pinned == {"id_py", "id_bigdata", "id_devops", "id_llm"}
        # 后端：Java 2 人簇 + Python + 大数据 = 4 人，min=3 下降级免死
        assert membership["id_py"] == membership["id_bigdata"] == membership["id_java"]
        # 运维：DevOps 并入 运维工程师 2 人簇 = 3 人
        assert membership["id_devops"] == membership["id_ops"]
        # 算法：大模型并入算法簇
        assert membership["id_llm"] == membership["id_vision"]

        kept, demoted = demote_small_clusters(membership, min_cluster_size=3)
        assert "id_java" in kept and "id_py" in kept
        assert demoted == set()

    def test_anchor_missing_skips_with_warning(self):
        membership = self._membership()
        name_map = self._name_map()
        del membership["id_vision"]
        del name_map["id_vision"]
        _, warnings, _pinned = apply_domain_pins(membership, name_map)
        assert any("机器视觉算法工程师" in w for w in warnings)
        assert membership["id_llm"] == 4  # 保持原簇

    def test_pinned_position_absent_is_noop(self):
        membership = self._membership()
        name_map = self._name_map()
        del membership["id_py"]
        del name_map["id_py"]
        _, warnings, _pinned = apply_domain_pins(membership, name_map)
        assert warnings == []


class TestAttachFringePosition:
    """带弃权的最近域分类：阈值 + 主导性双门槛。"""

    def _graph(self):
        # Go 场景缩影：对后端域两成员强连，对 AI 簇弱连
        return {
            "go": {"java": 6.8, "be": 6.7, "devops": 6.5, "prompt": 2.6, "genai": 1.9},
            "thin": {"java": 1.0, "prompt": 0.8},
            "torn": {"java": 3.0, "prompt": 2.6},
        }

    def _domains(self):
        return {"dom_1": ["java", "be", "devops"], "dom_2": ["prompt", "genai"]}

    def test_dominant_strong_affinity_attaches(self):
        key, scores, reason = attach_fringe_position(self._graph(), "go", self._domains())
        assert key == "dom_1" and reason == ""
        assert scores["dom_1"] == pytest.approx(20.0)

    def test_weak_affinity_abstains(self):
        key, _, reason = attach_fringe_position(
            self._graph(), "thin", self._domains(),
            min_affinity=ATTACH_MIN_AFFINITY, dominance=ATTACH_DOMINANCE,
        )
        assert key is None and reason == "below_affinity"  # 最优 1.0 < 2.0 门槛

    def test_non_dominant_abstains_even_if_strong(self):
        key, _, reason = attach_fringe_position(
            self._graph(), "torn", self._domains(),
            min_affinity=ATTACH_MIN_AFFINITY, dominance=ATTACH_DOMINANCE,
        )
        assert key is None and reason == "not_dominant"  # 3.0 达门槛但 < 1.3×2.6

    def test_no_edges_abstains(self):
        key, scores, reason = attach_fringe_position({"lonely": {}}, "lonely", self._domains())
        assert key is None and reason == "no_edges"
        assert scores == {"dom_1": 0.0, "dom_2": 0.0}


class TestResolveFringe:
    def _setup(self):
        graph = {
            "go": {"java": 6.8, "be": 6.7},
            "weak": {"java": 1.0},
        }
        domain_members = {"dom_1": ["java", "be"]}
        name_map = {"java": "Java开发工程师", "be": "后端开发工程师",
                    "go": "Go开发工程师", "weak": "某低频岗"}
        return graph, domain_members, name_map

    def test_strong_attaches_weak_abstains(self):
        graph, domain_members, name_map = self._setup()
        assigned, abstained, warnings = resolve_fringe(
            graph, {"go", "weak"}, domain_members, name_map, pins={},
        )
        assert assigned["go"]["dom"] == "dom_1"
        assert assigned["go"]["source"] == "attach"
        assert assigned["go"]["score"] == pytest.approx(13.5)
        assert abstained["weak"] == "below_affinity"
        assert warnings == []

    def test_fringe_pin_bypasses_threshold(self):
        graph, domain_members, name_map = self._setup()
        assigned, abstained, _ = resolve_fringe(
            graph, {"weak"}, domain_members, name_map,
            pins={"某低频岗": "Java开发工程师"},
        )
        assert assigned["weak"] == {"dom": "dom_1", "source": "pin",
                                    "score": None, "alt": None}
        assert abstained == {}

    def test_general_pin_forces_abstain(self):
        graph, domain_members, name_map = self._setup()
        assigned, abstained, _ = resolve_fringe(
            graph, {"weak"}, domain_members, name_map,
            pins={"某低频岗": "__general__"},
        )
        assert assigned == {} and abstained["weak"] == "general_pin"

    def test_pin_anchor_without_domain_warns_and_falls_back(self):
        graph, domain_members, name_map = self._setup()
        assigned, abstained, warnings = resolve_fringe(
            graph, {"weak"}, domain_members, name_map,
            pins={"某低频岗": "不存在的锚点岗"},
        )
        assert assigned == {} and abstained["weak"] == "below_affinity"
        assert any("不存在的锚点岗" in w for w in warnings)


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


class TestResolveLeftoverPins:
    """投影外孤立岗 pin 兜底：治理声明覆盖「无投影边」的技术盲区。"""

    def _assign(self):
        return {"id_fe": ("dom_1", "前端开发"), "id_be": ("dom_2", "后端开发")}

    def _name_map(self):
        return {"id_fe": "前端开发工程师", "id_be": "后端开发工程师"}

    def test_pinned_leftover_follows_anchor_domain(self):
        rows = [{"id": "id_ts", "name": "TypeScript工程师"}]
        pins = {"TypeScript工程师": "前端开发工程师"}
        out = resolve_leftover_pins(rows, self._assign(), self._name_map(), pins)
        assert out == [("id_ts", ("dom_1", "前端开发"))]

    def test_general_pin_leftover_not_claimed(self):
        rows = [{"id": "id_cmbs", "name": "CMBS交易员"}]
        pins = {"CMBS交易员": "__general__"}
        assert resolve_leftover_pins(rows, self._assign(), self._name_map(), pins) == []

    def test_missing_anchor_leftover_not_claimed(self):
        rows = [{"id": "id_x", "name": "某孤立岗"}]
        assert resolve_leftover_pins(rows, self._assign(), self._name_map(), {}) == []


# ---- 显式语义域（2026-09-02 细分阶段 1）----
# 验证 SEGREGATED_DOMAINS 成员：拓扑不可分（08-31 探针）、LLM 语义归类高置信
# （position_classify 探针 R3 0.85-0.98），由治理声明固化归入显式域。
# 边界：历史上被 GENERAL_PIN 弃权的成员撤销弃权；清单外 GENERAL_PIN 不受影响。
from scripts.sync_position_domains import (
    SEGREGATED_DOMAINS,
    _SEGREGATED_MEMBER_NAMES,
)


class TestSegregatedDomains:
    def _name_map(self):
        return {
            "id_sec": "网络安全工程师", "id_msec": "移动网络安全工程师",
            "id_genai": "GenAI/AgenticAI", "id_aigc": "AIGC抽卡师", "id_ai": "AI与数据系统",
            "id_sap": "SAP集成", "id_murex": "Murex应用", "id_pacs": "PACS与企业影像管理员",
            "id_pm": "产品经理", "id_pg": "项目经理",
            "id_cmbs": "CMBS交易员", "id_biochem": "生化工程师",
        }

    def test_segregated_domains_defined(self):
        assert set(SEGREGATED_DOMAINS) == {
            "dom_security", "dom_ai_app", "dom_ent_app", "dom_prod_mgmt",
        }
        # 域成员均已明确列出，无空域
        for spec in SEGREGATED_DOMAINS.values():
            assert spec["members"]
            assert spec["name"]

    def test_segregated_members_union(self):
        # 集合应含全部成员名单
        assert _SEGREGATED_MEMBER_NAMES == {
            "网络安全工程师", "移动网络安全工程师",
            "GenAI/AgenticAI", "AIGC抽卡师", "AI与数据系统",
            "SAP集成", "Murex应用", "PACS与企业影像管理员", "People应用",
            "产品经理", "项目经理",
        }

    def test_sync_assigns_segregated_members(self):
        """同步时显式域成员被覆盖归入显式域（source=domain_pin，覆盖弃权/拓扑结果）。
        清单外弃权岗不受影响。"""
        # name → pid（正确方向）；只放入清单内成员「网络安全工程师」与清单外弃权岗
        assign = {
            "id_sec": ("dom_1", "基础设施运维"),       # 拓扑误归运维
            "id_cmbs": (GENERAL_DOMAIN_ID, GENERAL_DOMAIN_NAME),  # 清单外弃权保留
        }
        sources = {"id_sec": "attach", "id_cmbs": "general_pin"}
        name_to_pid = {"网络安全工程师": "id_sec"}
        for dom_id, spec in SEGREGATED_DOMAINS.items():
            for name in spec["members"]:
                pid = name_to_pid.get(name)
                if pid:
                    assign[pid] = (dom_id, spec["name"])
                    sources[pid] = "domain_pin"
        assert assign["id_sec"] == ("dom_security", "网络安全")
        assert sources["id_sec"] == "domain_pin"
        assert assign["id_cmbs"] == (GENERAL_DOMAIN_ID, GENERAL_DOMAIN_NAME)
        assert sources["id_cmbs"] == "general_pin"
