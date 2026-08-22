"""置信度标量化单元测试（设计文档 7.2.4 节 + P1 证据距离优先）。

覆盖：
- graph_grounding_score：图谱证据距离分归一化（共享桥岗位数 → 0-1）
- evidence_score：graph_grounding 与 llm_logprob 各占 0.5 的融合（缺省中性 0.5）
- compute_confidence：中性证据不偏移既有 0.6/0.8 阈值（阈值校准）；
  证据 1.0 加分 / 0.0 减分；0-1 标量输出
"""

from app.services.discovery.confidence import (
    DEFAULT_W_EVIDENCE,
    REVIEW_BLOCK_THRESHOLD,
    compute_confidence,
    evidence_score,
    graph_grounding_score,
    wilson_lower,
)


class TestGraphGroundingScore:
    def test_empty_returns_none(self):
        """无技能/无图谱数据 → None（调用方按中性 0.5 处理，不惩罚不奖励）。"""
        assert graph_grounding_score([]) is None

    def test_zero_shared_is_isolated(self):
        """技能无任何既有岗位共用 → 证据距离远（0.0）。"""
        assert graph_grounding_score([0, 0]) == 0.0

    def test_half_saturation(self):
        """5 个共享岗位 / 饱和点 10 → 0.5。"""
        assert graph_grounding_score([5]) == 0.5

    def test_saturation_capped(self):
        """峰值超过饱和点封顶 1.0（10 个共享岗位即满分）。"""
        assert graph_grounding_score([3, 10]) == 1.0
        assert graph_grounding_score([25]) == 1.0

    def test_takes_max_across_skills(self):
        """取技能峰值：任一技能与大量既有岗位共用即视为证据距离近。"""
        assert graph_grounding_score([0, 8]) == 0.8


class TestEvidenceScore:
    def test_neutral_when_both_missing(self):
        """双信号缺失 → 中性 0.5（不偏移既有阈值）。"""
        assert evidence_score(None, None) == 0.5

    def test_full_evidence(self):
        """图谱证据 1.0 + LLM 概率 1.0 → 1.0。"""
        assert evidence_score(1.0, 1.0) == 1.0

    def test_zero_evidence(self):
        """双信号 0.0 → 0.0（完全无证据）。"""
        assert evidence_score(0.0, 0.0) == 0.0

    def test_half_weighted_blend(self):
        """单信号 1.0 另一缺失 → 0.75（各占 0.5，缺失按中性）。"""
        assert evidence_score(1.0, None) == 0.75
        assert evidence_score(None, 1.0) == 0.75

    def test_clamps_out_of_range(self):
        """越界值钳制到 [0, 1]：1.5→1.0，-1.0→0.0 → 0.5×1.0 + 0.5×0.0 = 0.5。"""
        assert evidence_score(1.5, -1.0) == 0.5


class TestComputeConfidence:
    def test_neutral_evidence_preserves_calibration(self):
        """图谱/Logprob 均未提供时 final == base + bonus（阈值校准不变）。

        jd=5(source 半饱和)+src=2(半)+growth=0.25(半) → base=0.5；单异常加分
        0.10 → 0.6（恰为 emerging 门槛，验证 0.6 阈值不被偏移）。
        """
        conf = compute_confidence(
            jd_count=5, source_count=2, growth_rate=0.25, arxiv_anomaly=True,
        )
        assert conf.evidence_score == 0.5
        assert conf.graph_grounding is None
        assert conf.llm_logprob is None
        assert abs(conf.final_confidence - (conf.base_confidence + conf.bonus)) < 1e-9
        assert conf.final_confidence >= 0.6

    def test_full_signal_caps_at_1(self):
        """全满信号 → 标量封顶 1.0。"""
        conf = compute_confidence(jd_count=10, source_count=4, growth_rate=1.0)
        assert conf.final_confidence == 1.0

    def test_graph_grounding_boosts(self):
        """图谱证据距离近（1.0，Logprob 未采集中性）→ 单信号极值增量为半权重。"""
        neutral = compute_confidence(jd_count=5, source_count=2, growth_rate=0.25)
        boosted = compute_confidence(
            jd_count=5, source_count=2, growth_rate=0.25, graph_grounding=1.0,
        )
        assert boosted.evidence_score == 0.75
        # evidence 0.75 → delta = w_evidence×(0.75-0.5)×2 = 0.5×w_evidence
        assert abs(boosted.final_confidence - neutral.final_confidence - 0.5 * DEFAULT_W_EVIDENCE) < 1e-9

    def test_graph_grounding_penalizes(self):
        """图谱证据距离远（0.0，技能孤立）→ 置信度按半权重降低。"""
        neutral = compute_confidence(jd_count=5, source_count=2, growth_rate=0.25)
        penalized = compute_confidence(
            jd_count=5, source_count=2, growth_rate=0.25, graph_grounding=0.0,
        )
        assert penalized.evidence_score == 0.25
        assert abs(neutral.final_confidence - penalized.final_confidence - 0.5 * DEFAULT_W_EVIDENCE) < 1e-9

    def test_llm_logprob_alone(self):
        """仅 LLM Logprob 0.0（生成自信用极低）→ 同样按半权重减分。"""
        neutral = compute_confidence(jd_count=5, source_count=2, growth_rate=0.25)
        low = compute_confidence(
            jd_count=5, source_count=2, growth_rate=0.25, llm_logprob=0.0,
        )
        assert abs(neutral.final_confidence - low.final_confidence - 0.5 * DEFAULT_W_EVIDENCE) < 1e-9

    def test_full_evidence_delta_is_full_weight(self):
        """双信号同向极值（证据 0/1）→ 增量为满权重 w_evidence。"""
        neutral = compute_confidence(jd_count=5, source_count=2, growth_rate=0.25)
        full = compute_confidence(
            jd_count=5, source_count=2, growth_rate=0.25,
            graph_grounding=1.0, llm_logprob=1.0,
        )
        none = compute_confidence(
            jd_count=5, source_count=2, growth_rate=0.25,
            graph_grounding=0.0, llm_logprob=0.0,
        )
        assert full.evidence_score == 1.0 and none.evidence_score == 0.0
        assert abs(full.final_confidence - neutral.final_confidence - DEFAULT_W_EVIDENCE) < 1e-9
        assert abs(neutral.final_confidence - none.final_confidence - DEFAULT_W_EVIDENCE) < 1e-9

    def test_evidence_breakdown_exposed(self):
        """证据距离明细（graph_grounding/llm_logprob/evidence_score）随标量输出。"""
        conf = compute_confidence(
            jd_count=3, source_count=1, growth_rate=0.1,
            graph_grounding=0.6, llm_logprob=0.4,
        )
        assert conf.graph_grounding == 0.6
        assert conf.llm_logprob == 0.4
        assert abs(conf.evidence_score - 0.5) < 1e-9  # 0.5×0.6 + 0.5×0.4

    def test_zero_inputs_with_far_evidence_clamped_to_zero(self, monkeypatch, tmp_path):
        """全零输入 + 孤立技能（证据 0.25）→ 钳制 0.0，不产负值（第五轮审查 P1-6 回归）。

        无钳制时 final = 0.15×(0.25−0.5)×2 = −0.075，被
        ConfidenceScore.final_confidence 的 Field(ge=0.0) 拒绝而崩 discovery
        worker；#388 后 w_evidence 运行时可调（0.4 即深负），故必须下界钳制。
        monkeypatch 配置路径强制默认权重，保证断言确定性。
        """
        monkeypatch.setattr(
            "app.services.discovery.confidence._CONFIG_PATH",
            tmp_path / "not_exists.json",
        )
        conf = compute_confidence(
            jd_count=0, source_count=0, growth_rate=0, graph_grounding=0.0,
        )
        assert conf.evidence_score == 0.25
        assert conf.final_confidence == 0.0

    def test_block_threshold_documented(self):
        """P1 阻断复核阈值恒为 0.75（与前端 REVIEW_BLOCK_THRESHOLD 同步）。"""
        assert REVIEW_BLOCK_THRESHOLD == 0.75

    def test_wilson_regression(self):
        """Wilson 冷启动兜底回归（不因证据距离改造而回归）。"""
        assert wilson_lower(1, 2) > 0
        assert wilson_lower(0, 0) == 0.0
