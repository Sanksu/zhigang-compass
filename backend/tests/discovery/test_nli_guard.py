"""NLI 跨文档矛盾检测单元测试（P0 幻觉防控软门控）。

覆盖三种矛盾信号（否定极性不对称 / 学历量级冲突 / 否定断言无基座）与
三分类（entailment / neutral / contradiction）判定口径。
"""

from app.services.discovery.nli_guard import (
    CONFIRMED_THRESHOLD,
    SUSPICIOUS_THRESHOLD,
    detect_contradiction,
)


class TestContradictionSignals:
    """三种矛盾信号的命中口径。"""

    def test_negation_asymmetry_confirmed(self):
        """同语言主题重合 + 极性翻转（肯定 vs 否定）→ 确认矛盾。"""
        r = detect_contradiction(
            "岗位要求具备 Python 编程能力，负责推荐系统开发。",
            "该岗位不需要 Python 编程能力。",
        )
        assert r.label == "contradiction"
        assert r.score >= CONFIRMED_THRESHOLD
        assert any("negation_asymmetry" in s for s in r.signals)

    def test_degree_level_conflict(self):
        """参考要求本科，草案称高中 → 量级冲突（≥2 级差）。"""
        r = detect_contradiction(
            "要求本科及以上学历，精通推荐算法。",
            "高中毕业即可，无需任何算法经验。",
        )
        assert r.label == "contradiction"
        assert any("degree_level_conflict" in s for s in r.signals)

    def test_adjacent_degree_not_conflict(self):
        """本科 vs 硕士（相邻量级）不判矛盾（防过度收紧）。"""
        r = detect_contradiction("要求本科及以上学历。", "要求硕士及以上学历。")
        assert r.label != "contradiction"

    def test_negation_assertion_suspicious(self):
        """草案含强否定且与基座几乎无重合（如跨语言场景）→ 可疑
        （重采样触发级，非确认矛盾）。"""
        r = detect_contradiction(
            "负责大数据平台架构设计与集群性能优化。",
            "该岗位无需任何编程能力。",
        )
        assert r.label != "contradiction"
        assert r.score >= SUSPICIOUS_THRESHOLD
        assert any("negation_assertion" in s for s in r.signals)


class TestThreeWayClassification:
    """entailment / neutral / contradiction 三分类。"""

    def test_entailment_high_overlap(self):
        """高度重合且无矛盾信号 → 蕴含（放行）。"""
        r = detect_contradiction(
            "负责推荐系统的设计与开发。",
            "负责推荐系统的开发与落地。",
        )
        assert r.label == "entailment"
        assert r.score < SUSPICIOUS_THRESHOLD

    def test_neutral_cross_language_translation(self):
        """跨语言翻译（英文基座 → 中文草案）重合度天然低 → 中性放行
        （无否定、无矛盾信号，不得误判）。"""
        r = detect_contradiction(
            "Design and develop software systems.",
            "负责开发与维护软件系统。",
        )
        assert r.label == "neutral"
        assert r.score < SUSPICIOUS_THRESHOLD

    def test_empty_inputs_neutral(self):
        assert detect_contradiction("", "x").label == "neutral"
        assert detect_contradiction("x", "").label == "neutral"
        assert detect_contradiction("", "").label == "neutral"

    def test_translation_without_negation_not_suspicious(self):
        """跨语言 + 草案无否定 → 不触发重采样（保持中性放行）。"""
        r = detect_contradiction(
            "Design and develop software systems.",
            "负责软件系统设计开发。",
        )
        assert r.score < SUSPICIOUS_THRESHOLD
