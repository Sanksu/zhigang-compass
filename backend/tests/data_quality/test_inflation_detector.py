"""通胀检测单元测试（设计文档 §4.8）。

覆盖四维评分边界、综合指数、分级与降权。
"""

import pytest

from app.services.data_quality.inflation_detector import (
    INFLATION_MILD_THRESHOLD,
    INFLATION_SEVERE_THRESHOLD,
    MILD_DECAY_WEIGHT,
    NORMAL_DECAY_WEIGHT,
    SEVERE_DECAY_WEIGHT,
    classify_inflation,
    compute_education_score,
    compute_experience_score,
    compute_inflation_score,
    compute_skill_count_score,
    compute_skill_depth_score,
)


# ───────────────────────── 经验维度 ─────────────────────────

class TestExperienceScore:
    def test_junior_within_ceiling_no_inflation(self):
        # 初级 ceiling=3
        assert compute_experience_score(3, "初级") == 0.0

    def test_junior_extreme_overflow_caps_at_one(self):
        # 初级岗要求 10 年大模型经验（设计文档 §4.8 典型样本）
        assert compute_experience_score(10, "初级") == 1.0

    def test_junior_mild_overflow(self):
        # ceiling=3, 要求 5 年 → overflow=2, 2/5=0.4
        assert compute_experience_score(5, "初级") == pytest.approx(0.4)

    def test_senior_high_ceiling_no_inflation(self):
        # 高级 ceiling=8
        assert compute_experience_score(8, "高级") == 0.0

    def test_expert_ceiling_allows_more(self):
        # 专家 ceiling=12
        assert compute_experience_score(12, "专家") == 0.0


# ───────────────────────── 技能数量维度 ─────────────────────────

class TestSkillCountScore:
    def test_junior_within_ceiling(self):
        assert compute_skill_count_score(5, "初级") == 0.0

    def test_junior_severe_overflow(self):
        # ceiling=5, 11 个技能 → overflow=6, 6/6=1.0
        assert compute_skill_count_score(11, "初级") == 1.0

    def test_senior_moderate_overflow(self):
        # 高级 ceiling=12, 15 个 → overflow=3, 3/6=0.5
        assert compute_skill_count_score(15, "高级") == 0.5


# ───────────────────────── 技能深度维度 ─────────────────────────

class TestSkillDepthScore:
    def test_junior_no_expert_skills(self):
        # 初级岗 ceiling=0，0 个精通不通胀
        assert compute_skill_depth_score(0, "初级") == 0.0

    def test_junior_three_expert_skills_severe(self):
        # overflow=3, 3/3=1.0
        assert compute_skill_depth_score(3, "初级") == 1.0

    def test_senior_allows_four_expert(self):
        # 高级 ceiling=4
        assert compute_skill_depth_score(4, "高级") == 0.0


# ───────────────────────── 学历维度 ─────────────────────────

class TestEducationScore:
    def test_junior_bachelor_no_inflation(self):
        # 初级 ceiling=2 (本科)
        assert compute_education_score("本科", "初级") == 0.0

    def test_junior_doctor_severe(self):
        # 博士 rank=4, ceiling=2, overflow=2, 2/2=1.0
        assert compute_education_score("博士", "初级") == 1.0

    def test_junior_master_mild(self):
        # 硕士 rank=3, ceiling=2, overflow=1, 1/2=0.5
        assert compute_education_score("硕士", "初级") == 0.5

    def test_unknown_education_treated_as_unlimited(self):
        assert compute_education_score("未知学历", "初级") == 0.0

    def test_expert_doctor_not_inflation(self):
        # 专家 ceiling=4 (博士)
        assert compute_education_score("博士", "专家") == 0.0


# ───────────────────────── 综合指数 ─────────────────────────

class TestClassifyInflation:
    def test_below_mild_threshold_normal(self):
        assert classify_inflation(0.3) == "normal"

    def test_at_mild_threshold_mild(self):
        # ≥ 0.4 即 mild
        assert classify_inflation(INFLATION_MILD_THRESHOLD) == "mild_inflation"

    def test_between_thresholds_mild(self):
        assert classify_inflation(0.55) == "mild_inflation"

    def test_above_severe_threshold_severe(self):
        assert classify_inflation(0.8) == "severe_inflation"

    def test_at_severe_threshold_mild(self):
        # 设计文档是 >0.7 即 severe，0.7 本身仍属 mild
        assert classify_inflation(INFLATION_SEVERE_THRESHOLD) == "mild_inflation"


# ───────────────────────── 综合场景 ─────────────────────────

class TestComputeInflationScore:
    def test_typical_inflation_case_junior_ten_years_master(self):
        """设计文档 §4.8 典型样本：初级岗要求 10 年大模型经验。"""
        result = compute_inflation_score(
            job_level="初级",
            min_years=10,
            skill_count=8,
            expert_level_count=3,
            education="硕士",
        )
        # 经验 1.0 + 数量 (8-5)/6=0.5 + 深度 1.0 + 学历 0.5 → 均权后 0.75
        assert result.experience_score == 1.0
        assert result.skill_depth_score == 1.0
        assert result.inflation_score == pytest.approx(0.75)
        assert result.label == "severe_inflation"
        assert result.decay_weight == SEVERE_DECAY_WEIGHT

    def test_reasonable_senior_no_inflation(self):
        """高级岗 8 年 + 12 技能 + 4 精通 + 本科 → 完全合理。"""
        result = compute_inflation_score(
            job_level="高级",
            min_years=8,
            skill_count=12,
            expert_level_count=4,
            education="本科",
        )
        assert result.inflation_score == 0.0
        assert result.label == "normal"
        assert result.decay_weight == NORMAL_DECAY_WEIGHT

    def test_mild_inflation_boundary(self):
        """中等级通胀触发 mild 降权 0.7。"""
        # 构造 inflation_score = 0.4：初级岗 5 年 + 6 技能 + 0 精通 + 本科
        # 经验 (5-3)/5=0.4 + 数量 (6-5)/6≈0.167 + 深度 0 + 学历 0
        # 均权后 0.25*0.4 + 0.25*0.167 = 0.1418 < 0.4 → normal
        # 调整：5 年 + 11 技能 → 经验 0.4 + 数量 1.0 → (0.4+1.0)/4=0.35 < 0.4
        # 再加 1 精通 → 深度 1/3 → (0.4+1.0+0.333)/4 ≈ 0.433 → mild
        result = compute_inflation_score(
            job_level="初级",
            min_years=5,
            skill_count=11,
            expert_level_count=1,
            education="本科",
        )
        assert result.label == "mild_inflation"
        assert result.decay_weight == MILD_DECAY_WEIGHT

    def test_all_four_dimensions_zero(self):
        result = compute_inflation_score(
            job_level="中级",
            min_years=3,
            skill_count=5,
            expert_level_count=0,
            education="本科",
        )
        assert result.experience_score == 0.0
        assert result.skill_count_score == 0.0
        assert result.skill_depth_score == 0.0
        assert result.education_score == 0.0
        assert result.inflation_score == 0.0
