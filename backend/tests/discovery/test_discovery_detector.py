"""新岗位发现检测单元测试（设计文档 7.2.3 节）。

覆盖 Z-score 门控、冷启动 Wilson 兜底、detect_candidates 组装。
"""

from app.services.discovery.confidence import wilson_lower
from app.services.discovery.detector import (
    CandidateProvider,
    DiscoveryDetector,
    DiscoveryInput,
    passes_cold_start_gate,
    passes_gate,
)
from app.services.discovery.schemas import DiscoveryFeatures, PositionState


def _features(
    z_score: float | None = 2.5,
    source_diversity: int = 2,
    jd_freq_ma3: float = 12,
) -> DiscoveryFeatures:
    return DiscoveryFeatures(
        jd_freq_ma3=jd_freq_ma3,
        z_score=z_score,
        source_diversity=source_diversity,
    )


class TestPassesGate:
    def test_strict_gate_passes(self):
        assert passes_gate(_features(z_score=3.0, source_diversity=2, jd_freq_ma3=10), 90) is True

    def test_strict_gate_fails_low_freq(self):
        assert passes_gate(_features(z_score=3.0, source_diversity=2, jd_freq_ma3=9), 90) is False

    def test_strict_gate_fails_low_diversity(self):
        assert passes_gate(_features(z_score=3.0, source_diversity=1, jd_freq_ma3=10), 90) is False

    def test_conservative_observation_passes(self):
        assert passes_gate(_features(z_score=1.8, source_diversity=2, jd_freq_ma3=5), 90) is True

    def test_conservative_fails_low_diversity(self):
        assert passes_gate(_features(z_score=1.8, source_diversity=1, jd_freq_ma3=5), 90) is False

    def test_none_zscore_fails(self):
        assert passes_gate(_features(z_score=None), 90) is False

    def test_below_conservative_fails(self):
        assert passes_gate(_features(z_score=1.0, source_diversity=3), 90) is False

    def test_cold_start_window_defers_to_wilson(self):
        """历史 < 60 天不走 Z-score 门控（返回 False 由上层走 wilson 兜底）。"""
        assert passes_gate(_features(z_score=3.0, source_diversity=3, jd_freq_ma3=12), 30) is False

    def test_exactly_sixty_days_uses_normal_gate(self):
        assert passes_gate(_features(z_score=3.0, source_diversity=2, jd_freq_ma3=10), 60) is True


class TestColdStartGate:
    def test_high_wilson_lower_passes(self):
        # wilson_lower(40, 50) ≈ 0.67 > 0.3
        assert wilson_lower(40, 50) > 0.3
        assert passes_cold_start_gate(40, 50, source_diversity=3) is True

    def test_low_success_ratio_fails(self):
        # wilson_lower(2, 50) ≈ 0.02 < 0.3
        assert passes_cold_start_gate(2, 50, source_diversity=3) is False

    def test_insufficient_diversity_fails(self):
        assert passes_cold_start_gate(40, 50, source_diversity=2) is False

    def test_zero_total_fails(self):
        assert passes_cold_start_gate(0, 0, source_diversity=3) is False


class TestDetectCandidates:
    class _FakeProvider(CandidateProvider):
        def __init__(self, inputs: list[DiscoveryInput]):
            self._inputs = inputs

        def iter_inputs(self):
            return iter(self._inputs)

    def test_only_gate_passing_inputs_become_candidates(self):
        detector = DiscoveryDetector()
        provider = self._FakeProvider(
            [
                DiscoveryInput("AIGC 工程师", _features(z_score=3.0, source_diversity=3, jd_freq_ma3=12), 90),
                DiscoveryInput("稳定岗位", _features(z_score=0.5, source_diversity=3), 90),
            ]
        )
        candidates = detector.detect_candidates(provider)
        assert [c.position_name for c in candidates] == ["AIGC 工程师"]

    def test_candidate_fields_are_populated(self):
        detector = DiscoveryDetector()
        provider = self._FakeProvider(
            [DiscoveryInput("RAG 工程师", _features(z_score=2.5, source_diversity=2, jd_freq_ma3=15), 90)]
        )
        candidates = detector.detect_candidates(provider)
        assert len(candidates) == 1
        cand = candidates[0]
        assert cand.state == PositionState.CANDIDATE
        assert cand.candidate_id.startswith("cand-")
        assert cand.features.z_score == 2.5
        assert cand.detected_at  # 非空 ISO8601
        assert cand.seed_matched is False
        assert cand.rag_matched is False

    def test_cold_start_route_promotes_via_wilson(self):
        detector = DiscoveryDetector()
        provider = self._FakeProvider(
            [
                DiscoveryInput(
                    "向量数据库工程师",
                    _features(z_score=None, source_diversity=3, jd_freq_ma3=3),
                    history_days=30,
                    cold_successes=40,
                    cold_total=50,
                )
            ]
        )
        candidates = detector.detect_candidates(provider)
        assert [c.position_name for c in candidates] == ["向量数据库工程师"]

    def test_cold_start_without_stats_not_promoted(self):
        detector = DiscoveryDetector()
        provider = self._FakeProvider(
            [DiscoveryInput("未知岗位", _features(z_score=None, source_diversity=3), 30)]
        )
        candidates = detector.detect_candidates(provider)
        assert candidates == []

    def test_empty_provider_returns_empty(self):
        detector = DiscoveryDetector()
        candidates = detector.detect_candidates(self._FakeProvider([]))
        assert candidates == []
