"""演化检测单元测试（设计文档 7.1 节）。

覆盖 Z-score 计算、趋势分类边界、小基数保护、环比、批量检测。
"""

import pytest

from app.services.evolution.detector import (
    EvolutionDetector,
    WindowProvider,
    classify_trend,
    compute_zscore,
)
from app.services.evolution.schemas import (
    SkillEvolutionTrend,
    SkillFrequencyWindow,
)


def _window(freq: int, skill_id: str = "sk1", skill_name: str = "Python") -> SkillFrequencyWindow:
    return SkillFrequencyWindow(
        skill_id=skill_id,
        skill_name=skill_name,
        window_start="2026-01-01",
        window_end="2026-01-30",
        frequency=freq,
    )


class TestZScore:
    def test_std_zero_returns_zero(self):
        assert compute_zscore(100, 50, 0) == 0.0

    def test_above_mean_positive(self):
        assert compute_zscore(30, 20, 5) == 2.0

    def test_below_mean_negative(self):
        assert compute_zscore(10, 20, 5) == -2.0


class TestClassifyTrend:
    def test_emerging_above_two(self):
        assert classify_trend(2.1, 50) == SkillEvolutionTrend.EMERGING

    def test_z_two_point_zero_is_rising_not_emerging(self):
        # 阈值严格大于：z=2.0 不触发 emerging
        assert classify_trend(2.0, 50) == SkillEvolutionTrend.RISING

    def test_rising_above_one_five(self):
        assert classify_trend(1.6, 50) == SkillEvolutionTrend.RISING

    def test_declining_below_minus_one_five(self):
        assert classify_trend(-1.6, 50) == SkillEvolutionTrend.DECLINING

    def test_stable_in_middle(self):
        assert classify_trend(0.5, 50) == SkillEvolutionTrend.STABLE

    def test_low_frequency_protected(self):
        assert classify_trend(3.0, 5) == SkillEvolutionTrend.PROTECTED

    def test_explicit_protected_flag(self):
        assert classify_trend(3.0, 50, protected=True) == SkillEvolutionTrend.PROTECTED


class TestDetectSkill:
    def test_no_history_is_stable_baseline(self):
        detector = EvolutionDetector()
        signal = detector.detect_skill("sk1", _window(50), [])
        assert signal.trend == SkillEvolutionTrend.STABLE
        assert signal.z_score is None
        assert signal.mom_growth is None
        assert signal.confidence == 0.0

    def test_emerging_with_positive_zscore(self):
        detector = EvolutionDetector()
        signal = detector.detect_skill(
            "sk1",
            _window(100),
            [_window(10), _window(20), _window(30)],
        )
        assert signal.trend == SkillEvolutionTrend.EMERGING
        assert signal.z_score is not None
        assert signal.mom_growth == pytest.approx(7 / 3, abs=1e-3)
        assert signal.historical_mean == pytest.approx(20.0)
        assert signal.confidence > 0.0

    def test_declining_with_negative_zscore(self):
        detector = EvolutionDetector()
        signal = detector.detect_skill(
            "sk1",
            _window(30),
            [_window(90), _window(110)],
        )
        assert signal.trend == SkillEvolutionTrend.DECLINING
        assert signal.z_score < 0

    def test_low_frequency_protection_hides_zscore(self):
        detector = EvolutionDetector()
        signal = detector.detect_skill(
            "sk1",
            _window(5),
            [_window(50), _window(60)],
        )
        assert signal.trend == SkillEvolutionTrend.PROTECTED
        assert signal.z_score is None
        assert signal.confidence == 0.0

    def test_single_history_window_std_zero(self):
        detector = EvolutionDetector()
        signal = detector.detect_skill("sk1", _window(60), [_window(50)])
        assert signal.historical_std == 0.0
        assert signal.trend == SkillEvolutionTrend.STABLE

    def test_flat_history_gives_zero_zscore(self):
        detector = EvolutionDetector()
        signal = detector.detect_skill("sk1", _window(50), [_window(50), _window(50)])
        assert signal.z_score == 0.0
        assert signal.trend == SkillEvolutionTrend.STABLE

    def test_zero_previous_window_mom_is_none(self):
        detector = EvolutionDetector()
        signal = detector.detect_skill("sk1", _window(60), [_window(50), _window(0)])
        assert signal.mom_growth is None


class TestDetectBatch:
    class _FakeProvider(WindowProvider):
        def __init__(self, windows_by_id: dict):
            self._windows = windows_by_id

        def get_windows(self, skill_id):
            return self._windows[skill_id]

    def test_batch_detects_all_skills(self):
        detector = EvolutionDetector()
        provider = self._FakeProvider(
            {
                "sk1": (_window(100), [_window(10), _window(20)]),
                "sk2": (_window(5), [_window(50), _window(60)]),
            }
        )
        signals = detector.detect_batch(["sk1", "sk2"], provider)
        assert len(signals) == 2
        assert signals[0].skill_id == "sk1"
        assert signals[0].trend == SkillEvolutionTrend.EMERGING
        assert signals[1].trend == SkillEvolutionTrend.PROTECTED

    def test_batch_without_provider_raises(self):
        detector = EvolutionDetector()
        with pytest.raises(ValueError):
            detector.detect_batch(["sk1"], None)
