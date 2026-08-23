"""时滞检测单元测试（设计文档 §4.7）。

覆盖 SAI 阈值、Jaccard 边界、僵尸 JD、抄袭时滞、降权叠加。
"""

from datetime import date, timedelta

from app.services.data_quality.temporal_detector import (
    FRESH_DECAY_WEIGHT,
    OBSOLETE_DECAY_WEIGHT,
    PLAGIARISM_DECAY_WEIGHT,
    PLAGIARISM_DAYS_THRESHOLD,
    SAI_OBSOLETE_THRESHOLD,
    SAI_STALE_THRESHOLD,
    STALE_DECAY_WEIGHT,
    ZOMBIE_CONSECUTIVE_PERIODS,
    ZOMBIE_DECAY_WEIGHT,
    _count_consecutive_similar,
    apply_temporal_decay,
    classify_sai,
    compute_jaccard,
    compute_sai,
    detect_plagiarism,
    detect_zombie_jd,
)
from app.services.data_quality.schemas import JDSkillSet


# ───────────────────────── Jaccard ─────────────────────────

class TestJaccard:
    def test_identical_sets_returns_one(self):
        assert compute_jaccard({"Python", "Java"}, {"Java", "Python"}) == 1.0

    def test_disjoint_sets_returns_zero(self):
        assert compute_jaccard({"Python"}, {"Java"}) == 0.0

    def test_both_empty_returns_zero(self):
        assert compute_jaccard(set(), set()) == 0.0

    def test_partial_overlap_returns_ratio(self):
        # |A∩B|=2, |A∪B|=4 → 0.5
        assert compute_jaccard({"Python", "Java", "Go"}, {"Python", "Java", "Rust"}) == 0.5

    def test_subset_returns_ratio(self):
        # |A∩B|=2, |A∪B|=3 → 2/3
        result = compute_jaccard({"Python", "Java"}, {"Python", "Java", "Go"})
        assert abs(result - 2 / 3) < 1e-6


# ───────────────────────── SAI ─────────────────────────

class TestSAI:
    def test_jd_older_than_baseline_sai_above_one(self):
        # JD 技能年龄中位数 200 天 / 同岗位近 90 天中位数 100 天 → SAI=2.0
        sai = compute_sai([200, 200], [100, 100])
        assert sai == 2.0

    def test_jd_newer_than_baseline_sai_below_one(self):
        sai = compute_sai([50, 50], [100, 100])
        assert sai == 0.5

    def test_empty_jd_skills_returns_zero(self):
        assert compute_sai([], [100, 100]) == 0.0

    def test_empty_baseline_returns_zero(self):
        assert compute_sai([100, 100], []) == 0.0

    def test_zero_baseline_median_returns_zero_avoid_div_zero(self):
        # 岗位参考与 JD 技能首见时长均为 0（数据不足）→ 不除零，返回 0 不武断判定
        assert compute_sai([0, 0], [0, 0]) == 0.0

    def test_zero_baseline_jd_older_flagged_obsolete(self):
        # 岗位近 90 天参考技能全为新技能（median=0），JD 技能中位数 100 天
        # → 技能相对岗位明显更旧，视为过时（越 obsolete 阈值，而非"新鲜"）
        sai = compute_sai([100], [0, 0])
        assert sai > SAI_OBSOLETE_THRESHOLD
        assert classify_sai(sai).label == "content_obsolete"


class TestClassifySAI:
    def test_fresh_below_stale_threshold(self):
        result = classify_sai(1.0)
        assert result.label == "fresh"
        assert result.decay_weight == FRESH_DECAY_WEIGHT

    def test_stale_between_thresholds(self):
        result = classify_sai(1.7)
        assert result.label == "content_stale"
        assert result.decay_weight == STALE_DECAY_WEIGHT

    def test_obsolete_above_obsolete_threshold(self):
        result = classify_sai(2.5)
        assert result.label == "content_obsolete"
        assert result.decay_weight == OBSOLETE_DECAY_WEIGHT

    def test_boundary_just_above_stale(self):
        # SAI=1.5 不应判 stale（设计文档是 >1.5）
        assert classify_sai(SAI_STALE_THRESHOLD).label == "fresh"

    def test_boundary_just_above_obsolete(self):
        assert classify_sai(SAI_OBSOLETE_THRESHOLD).label == "content_stale"


# ───────────────────────── 僵尸 JD ─────────────────────────

class TestZombieJD:
    def _skills(self, seed: str, count: int = 5) -> set[str]:
        return {f"{seed}_{i}" for i in range(count)}

    def test_consecutive_four_periods_with_stale_sai_marks_zombie(self):
        # 4 个历史周期技能集合完全相同 + SAI=1.6
        history = [self._skills("s", 5) for _ in range(4)]
        current = self._skills("s", 5)
        result = detect_zombie_jd(history, current, sai=1.6)
        assert result.is_zombie is True
        assert result.decay_weight == ZOMBIE_DECAY_WEIGHT
        assert result.jaccard == 1.0
        assert result.consecutive_periods == 5  # 4 历史 + 1 当前

    def test_insufficient_periods_not_zombie(self):
        history = [self._skills("s", 5) for _ in range(2)]
        current = self._skills("s", 5)
        result = detect_zombie_jd(history, current, sai=1.6)
        assert result.is_zombie is False
        assert result.decay_weight == FRESH_DECAY_WEIGHT

    def test_fresh_sai_not_zombie_even_if_jaccard_high(self):
        # 技能集合相似但 SAI 低 → 技能在更新，不算僵尸
        history = [self._skills("s", 5) for _ in range(4)]
        current = self._skills("s", 5)
        result = detect_zombie_jd(history, current, sai=1.0)
        assert result.is_zombie is False

    def test_jaccard_below_threshold_breaks_streak(self):
        # 历史中第 3 个周期技能变了 → 连续计数应在第 3 周期中断
        history = [
            self._skills("s", 5),
            self._skills("s", 5),
            self._skills("other", 5),  # 这一周期变了
            self._skills("s", 5),
        ]
        current = self._skills("s", 5)
        result = detect_zombie_jd(history, current, sai=1.6)
        # 当前与最后 1 个历史相同 → consecutive=2，<4 → 非 zombie
        assert result.consecutive_periods == 2
        assert result.is_zombie is False

    def test_explicit_consecutive_periods_overrides_counting(self):
        # 显式传入 consecutive_periods=4 且 SAI=1.6 → zombie
        result = detect_zombie_jd(
            history_jd_skills=[],
            current_jd_skills=self._skills("s", 5),
            sai=1.6,
            consecutive_periods=ZOMBIE_CONSECUTIVE_PERIODS,
        )
        assert result.is_zombie is True

    def test_empty_history_not_zombie(self):
        result = detect_zombie_jd([], {"Python"}, sai=2.0)
        assert result.is_zombie is False
        assert result.jaccard == 0.0


class TestCountConsecutiveSimilar:
    def test_empty_history_returns_one(self):
        assert _count_consecutive_similar([], {"Python"}, 0.95) == 1

    def test_all_similar_returns_full_count(self):
        history = [{"Python"} for _ in range(3)]
        assert _count_consecutive_similar(history, {"Python"}, 0.95) == 4

    def test_breaks_at_first_dissimilar(self):
        history = [{"Python"}, {"Java"}]  # 尾部 Java 与当前 Python 不相似
        assert _count_consecutive_similar(history, {"Python"}, 0.95) == 1


# ───────────────────────── 抄袭时滞 ─────────────────────────

class TestPlagiarism:
    def _jd(self, skills: list[str], days_ago: int) -> JDSkillSet:
        return JDSkillSet(
            jd_id=f"jd_{days_ago}",
            position_name="后端开发工程师",
            company="某公司",
            publish_date=date.today() - timedelta(days=days_ago),
            skills=skills,
        )

    def test_subset_with_long_interval_marks_plagiarism(self):
        old = self._jd(["Python", "Java", "Go", "Rust"], days_ago=120)
        new = self._jd(["Python", "Java"], days_ago=0)  # 新 JD 是旧子集
        result = detect_plagiarism(new, old)
        assert result.is_plagiarism is True
        assert result.is_subset is True
        assert result.days_interval > PLAGIARISM_DAYS_THRESHOLD
        assert result.decay_weight == PLAGIARISM_DECAY_WEIGHT

    def test_not_subset_not_plagiarism(self):
        old = self._jd(["Python", "Java"], days_ago=120)
        new = self._jd(["Python", "Java", "Go"], days_ago=0)  # 新增技能
        result = detect_plagiarism(new, old)
        assert result.is_plagiarism is False
        assert result.is_subset is False

    def test_short_interval_not_plagiarism(self):
        old = self._jd(["Python", "Java"], days_ago=30)
        new = self._jd(["Python"], days_ago=0)
        result = detect_plagiarism(new, old)
        assert result.is_plagiarism is False
        assert result.days_interval <= PLAGIARISM_DAYS_THRESHOLD

    def test_empty_new_skills_not_subset(self):
        old = self._jd(["Python"], days_ago=120)
        new = self._jd([], days_ago=0)
        result = detect_plagiarism(new, old)
        # 空集合 issubset 任何集合都为 True，但语义上不算抄袭
        assert result.is_plagiarism is False

    def test_negative_interval_when_new_older_than_old(self):
        # 新 JD 比旧 JD 还早（异常场景）：days_interval 为负，不触发
        old = self._jd(["Python", "Java"], days_ago=10)
        new = self._jd(["Python"], days_ago=200)
        result = detect_plagiarism(new, old)
        assert result.is_plagiarism is False


# ───────────────────────── 降权叠加 ─────────────────────────

class TestApplyTemporalDecay:
    def test_base_weight_alone(self):
        assert apply_temporal_decay(1.0) == 1.0

    def test_takes_minimum_across_multiple_decays(self):
        from app.services.data_quality.schemas import SAIResult, ZombieJDResult

        sai = SAIResult(sai=2.5, label="content_obsolete", decay_weight=0.0)
        zombie = ZombieJDResult(
            is_zombie=True, jaccard=0.96, consecutive_periods=4, decay_weight=0.3
        )
        # stale=0.5, zombie=0.3, base=1.0 → 取最小 0.0（obsolete 归档）
        result = apply_temporal_decay(1.0, sai_result=sai, zombie_result=zombie)
        assert result == 0.0

    def test_fresh_results_keep_base_weight(self):
        from app.services.data_quality.schemas import SAIResult

        sai = SAIResult(sai=1.0, label="fresh", decay_weight=1.0)
        assert apply_temporal_decay(0.8, sai_result=sai) == 0.8


# ───────────────────────── 窗口判断 ─────────────────────────
