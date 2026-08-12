"""技能簇后处理与 LLM 兜底单元测试（图算法优化方案 §4）。

覆盖：
- 规则 1 孤立簇剔除：无跨簇边的小簇标记 orphan，不移入业务视图
- 规则 2 过小簇合并：成员 ≤ 2 且无主导技能 → 并入共现权重最大相邻簇
- 规则 3 规则标签：按共现权重 top-3 拼接
- LLM 触发条件：无主导技能 / 跨类别 / 标签为空
- LLM prompt 构造与失败降级
"""

import pytest

from app.services.graph_algorithms.cluster_llm import (
    ClusterLLMClassifier,
    ClusterLLMDecision,
    build_cluster_prompt,
)
from app.services.graph_algorithms.postprocess import (
    ClusterPostProcessor,
    rule_label,
)


def _weights():
    """手搓共现网络：A-B 强簇 / C-D 强簇，C-D 与 A-B 弱连接；E 孤立。"""
    return {
        "A": {"B": 3.0, "C": 0.2},
        "B": {"A": 3.0, "C": 0.2},
        "C": {"D": 2.0, "A": 0.2, "B": 0.2},
        "D": {"C": 2.0},
        "E": {},
    }


class TestRulePostProcess:
    def test_orphan_cluster_marked(self):
        # E 孤立（无任何边）→ orphan
        clusters = {"A": 0, "B": 0, "C": 1, "D": 1, "E": 2}
        out = ClusterPostProcessor().process(clusters, _weights(), skill_names={})
        orphan = [c for c in out["clusters"] if c["orphan"]]
        assert len(orphan) == 1
        assert orphan[0]["skills"] == ["E"]
        assert orphan[0]["label"] == ""  # 孤立簇不参与标签生成
        assert out["orphaned"] == [2]

    def test_small_cluster_merged_into_strongest_neighbor(self):
        # F 与 C 有弱边 0.3，簇 {F} 成员=1 ≤ 2 且无主导技能 → 并入 C 簇
        weights = {
            "A": {"B": 3.0},
            "B": {"A": 3.0},
            "C": {"D": 2.0, "F": 0.3},
            "D": {"C": 2.0},
            "F": {"C": 0.3},
        }
        clusters = {"A": 0, "B": 0, "C": 1, "D": 1, "F": 2}
        out = ClusterPostProcessor().process(clusters, weights, skill_names={})
        # F 并入 C 簇（cluster 1）
        merged = out["merged"]
        assert merged == [2]
        c1 = next(c for c in out["clusters"] if c["cluster_id"] == 1)
        assert "F" in c1["skills"]

    def test_no_merge_when_small_cluster_has_no_cross_edges(self):
        # 孤立簇（无跨簇边）不合并，保持原簇并标记 orphan
        clusters = {"A": 0, "B": 0, "E": 1}
        out = ClusterPostProcessor().process(clusters, _weights(), skill_names={})
        assert out["merged"] == []
        e_cluster = next(c for c in out["clusters"] if c["cluster_id"] == 1)
        assert e_cluster["orphan"] is True

    def test_rule_label_top3_by_weight(self):
        # 权重降序：A(3.2) / B(3.2) / C(2.4) / D(2.0) → top3 = A·B·C
        weights = {
            "A": {"B": 3.0, "C": 0.2},
            "B": {"A": 3.0, "C": 0.2},
            "C": {"D": 2.0, "A": 0.2, "B": 0.2},
            "D": {"C": 2.0},
        }
        label = rule_label(["A", "B", "C", "D"], {"A": "Python", "B": "Django", "C": "SQL", "D": "MySQL"}, weights)
        assert label == "Python·Django·SQL"

    def test_no_dominant_skill_triggers_llm(self):
        # 权重分散：无 top-1 占比 ≥ 0.4 → needs_llm
        weights = {
            "A": {"B": 1.0, "C": 1.0},
            "B": {"A": 1.0, "C": 1.0},
            "C": {"A": 1.0, "B": 1.0},
        }
        clusters = {"A": 0, "B": 0, "C": 0}
        out = ClusterPostProcessor().process(clusters, weights, skill_names={})
        c0 = out["clusters"][0]
        assert c0["needs_llm"] is True
        assert "no_dominant_skill" in c0["triggers"]

    def test_dominant_skill_does_not_trigger(self):
        # A 明显主导（权重占比 ≥ 0.4）→ 不触发
        weights = {
            "A": {"B": 3.0, "C": 0.5},
            "B": {"A": 3.0},
            "C": {"A": 0.5},
        }
        clusters = {"A": 0, "B": 0, "C": 0}
        out = ClusterPostProcessor().process(clusters, weights, skill_names={})
        c0 = out["clusters"][0]
        assert c0["needs_llm"] is False

    def test_cross_category_triggers_llm(self):
        # 技能跨 3 个类别 → 触发 cross_category
        weights = {
            "A": {"B": 1.0, "C": 1.0},
            "B": {"A": 1.0},
            "C": {"A": 1.0},
        }
        clusters = {"A": 0, "B": 0, "C": 0}
        cats = {"A": "编程语言", "B": "数据库", "C": "云原生"}
        out = ClusterPostProcessor().process(clusters, weights, skill_names={"A": "A", "B": "B", "C": "C"}, categories=cats)
        c0 = out["clusters"][0]
        assert "cross_category" in c0["triggers"]

    def test_cross_category_within_limit_no_trigger(self):
        weights = {
            "A": {"B": 1.0, "C": 1.0},
            "B": {"A": 1.0},
            "C": {"A": 1.0},
        }
        clusters = {"A": 0, "B": 0, "C": 0}
        # 仅 2 类别，低于 LLM_CROSS_CATEGORY=3，且权重占比均衡 → 无 cross_category
        cats = {"A": "编程语言", "B": "编程语言", "C": "数据库"}
        out = ClusterPostProcessor().process(clusters, weights, skill_names={"A": "A", "B": "B", "C": "C"}, categories=cats)
        c0 = out["clusters"][0]
        assert "cross_category" not in c0["triggers"]


class TestClusterLLM:
    def test_build_prompt_contains_skills_and_triggers(self):
        prompt = build_cluster_prompt(["Python", "Django"], ["no_dominant_skill"])
        assert "Python" in prompt and "Django" in prompt
        assert "no_dominant_skill" in prompt

    def test_decision_schema_defaults(self):
        # JSON Schema 约束：coherent 必填，cluster_name/splits 有默认
        d = ClusterLLMDecision(coherent=False, splits=["数据工程", "Web"])
        assert d.coherent is False
        assert d.splits == ["数据工程", "Web"]
        assert d.cluster_name is None

    def test_classify_when_provider_unconfigured(self, monkeypatch):
        # LLM 未配置（provider 构造抛 LLMConfigurationError）→ 降级规则标签
        from app.services.extraction.llm_provider import LLMConfigurationError

        import app.services.graph_algorithms.cluster_llm as cluster_llm_mod

        def _no_config():
            raise LLMConfigurationError("无 LLM 配置")

        monkeypatch.setattr(cluster_llm_mod, "LLMProviderChain", _no_config)
        classifier = ClusterLLMClassifier()
        assert classifier._llm is None
        dec = classifier.classify(["Python", "Django"], ["no_dominant_skill"], rule_label="Python·Django")
        assert dec.coherent is True
        assert dec.cluster_name == "Python·Django"
        assert dec.splits == []

    def test_classify_empty_skills(self):
        classifier = ClusterLLMClassifier(llm=None)
        dec = classifier.classify([], [], rule_label="")
        assert dec.coherent is True
        assert dec.cluster_name is None

    def test_llm_failure_falls_back_to_rule_label(self):
        from app.services.extraction.llm_provider import LLMExtractionError

        class _FailingLLM:
            def extract_structured(self, prompt, model, **kw):
                raise LLMExtractionError("provider 调用失败")

        classifier = ClusterLLMClassifier(llm=_FailingLLM())
        dec = classifier.classify(["Python"], ["empty_label"], rule_label="Python")
        # LLMExtractionError 被捕获 → 降级规则标签，不抛异常
        assert dec.coherent is True
        assert dec.cluster_name == "Python"

    def test_non_llm_error_propagates(self):
        # 非 LLMExtractionError 的异常应向上抛（fail-fast，不吞非预期错误）
        class _BrokenLLM:
            def extract_structured(self, prompt, model, **kw):
                raise RuntimeError("unexpected")

        classifier = ClusterLLMClassifier(llm=_BrokenLLM())
        with pytest.raises(RuntimeError):
            classifier.classify(["Python"], ["empty_label"], rule_label="Python")
