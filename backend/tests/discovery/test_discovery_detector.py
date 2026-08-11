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

    def test_zscore_used_even_with_short_history(self):
        """修复（2026-08-09）：z_score 非 None 时即用 Z-score 门控，与历史天数无关。

        此前 history_days < 60 一律禁用 Z-score，导致快照跨度 <60 天时即使
        z_score 已由多期快照算出也被强制走冷启动，candidate 发现失效。
        """
        assert passes_gate(_features(z_score=3.0, source_diversity=3, jd_freq_ma3=12), 30) is True
        # 保守门控同样不受历史天数限制
        assert passes_gate(_features(z_score=1.8, source_diversity=2, jd_freq_ma3=5), 10) is True

    def test_exactly_sixty_days_uses_normal_gate(self):
        assert passes_gate(_features(z_score=3.0, source_diversity=2, jd_freq_ma3=10), 60) is True


class TestColdStartGate:
    def test_high_wilson_lower_passes(self):
        # wilson_lower(40, 50) ≈ 0.67 > 0.2（2026-08-11 阈值 0.3→0.2）
        assert wilson_lower(40, 50) > 0.2
        assert passes_cold_start_gate(40, 50, source_diversity=3) is True

    def test_low_success_ratio_fails(self):
        # wilson_lower(2, 50) ≈ 0.02 < 0.2
        assert passes_cold_start_gate(2, 50, source_diversity=2) is False

    def test_insufficient_diversity_fails(self):
        # 2026-08-11 源多样性阈值 3→2，1 源仍不过
        assert passes_cold_start_gate(40, 50, source_diversity=1) is False

    def test_zero_total_fails(self):
        assert passes_cold_start_gate(0, 0, source_diversity=3) is False

    def test_first_window_appearance_passes(self):
        # 首现即出现 1 个窗口：wilson_lower(1, 1) ≈ 0.206 > 0.2
        # 原 0.3 阈值下不过（≈0.206 < 0.3），低频新岗位需等第 2 个窗口；
        # 调降后允许首窗口确认即入池
        assert passes_cold_start_gate(1, 1, source_diversity=2) is True

    def test_low_appearance_ratio_fails(self):
        # 首现后 2 窗口仅出现 1：wilson_lower(1, 2) ≈ 0.011 < 0.2
        assert passes_cold_start_gate(1, 2, source_diversity=2) is False


class _FakeProvider(CandidateProvider):
    """测试用候选数据源。"""

    def __init__(self, inputs: list[DiscoveryInput]):
        self._inputs = inputs

    def iter_inputs(self):
        return iter(self._inputs)


class TestDetectCandidates:
    def test_only_gate_passing_inputs_become_candidates(self):
        detector = DiscoveryDetector()
        provider = _FakeProvider(
            [
                DiscoveryInput("AIGC 工程师", _features(z_score=3.0, source_diversity=3, jd_freq_ma3=12), 90),
                DiscoveryInput("稳定岗位", _features(z_score=0.5, source_diversity=3), 90),
            ]
        )
        candidates = detector.detect_candidates(provider)
        assert [c.position_name for c in candidates] == ["AIGC 工程师"]

    def test_candidate_fields_are_populated(self):
        detector = DiscoveryDetector()
        provider = _FakeProvider(
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
        provider = _FakeProvider(
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
        provider = _FakeProvider(
            [DiscoveryInput("未知岗位", _features(z_score=None, source_diversity=3), 30)]
        )
        candidates = detector.detect_candidates(provider)
        assert candidates == []

    def test_empty_provider_returns_empty(self):
        detector = DiscoveryDetector()
        candidates = detector.detect_candidates(_FakeProvider([]))
        assert candidates == []


class TestMaturePositionExclusion:
    """成熟岗位排除（2026-08-11）：岗位首见早于观测起点视为存量，两条门控路径都拦截。"""

    def test_high_zscore_but_mature_is_excluded(self):
        # Z-score 再高，采集存量（首见早于观测起点）也不进入候选池
        detector = DiscoveryDetector()
        provider = _FakeProvider(
            [
                DiscoveryInput(
                    "算法工程师",
                    _features(z_score=3.0, source_diversity=3, jd_freq_ma3=50),
                    90,
                    first_seen_date="2026-08-01",
                    observation_start="2026-08-02",
                )
            ]
        )
        assert detector.detect_candidates(provider) == []

    def test_mature_excluded_via_cold_start_path(self):
        # 冷启动路径同样被成熟排除拦截
        detector = DiscoveryDetector()
        provider = _FakeProvider(
            [
                DiscoveryInput(
                    "后端开发工程师",
                    _features(z_score=None, source_diversity=2, jd_freq_ma3=3),
                    30,
                    cold_successes=2,
                    cold_total=2,
                    first_seen_date="2026-08-01",
                    observation_start="2026-08-02",
                )
            ]
        )
        assert detector.detect_candidates(provider) == []

    def test_new_position_not_excluded(self):
        # 观测窗口内首次出现的岗位正常走门控
        detector = DiscoveryDetector()
        provider = _FakeProvider(
            [
                DiscoveryInput(
                    "新出现岗位",
                    _features(z_score=3.0, source_diversity=2, jd_freq_ma3=10),
                    90,
                    first_seen_date="2026-08-10",
                    observation_start="2026-08-02",
                )
            ]
        )
        candidates = detector.detect_candidates(provider)
        assert [c.position_name for c in candidates] == ["新出现岗位"]

    def test_missing_observation_start_not_excluded(self):
        # 无快照（无观测起点）时不做成熟排除，避免误伤
        detector = DiscoveryDetector()
        provider = _FakeProvider(
            [
                DiscoveryInput(
                    "历史岗位",
                    _features(z_score=3.0, source_diversity=2, jd_freq_ma3=10),
                    90,
                    first_seen_date="2026-01-01",
                    observation_start=None,
                )
            ]
        )
        assert len(detector.detect_candidates(provider)) == 1

    def test_cold_start_first_window_new_position_promotes(self):
        # 新岗位首现 1 个窗口 + 双源 → 冷启动通过（原 0.3 阈值下
        # wilson_lower(1, 1) ≈ 0.206 不过，调降 0.2 后首窗口确认即入池）
        detector = DiscoveryDetector()
        provider = _FakeProvider(
            [
                DiscoveryInput(
                    "新出现低频岗位",
                    _features(z_score=None, source_diversity=2, jd_freq_ma3=3),
                    30,
                    cold_successes=1,
                    cold_total=1,
                    first_seen_date="2026-08-10",
                    observation_start="2026-08-02",
                )
            ]
        )
        candidates = detector.detect_candidates(provider)
        assert [c.position_name for c in candidates] == ["新出现低频岗位"]
