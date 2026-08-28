"""岗位技能熟练度等级规范化单元测试（M5 测试补充）。

覆盖 proficiency 模块的四个核心函数，包括：
- normalize_proficiency_level：等级归一化
- has_proficiency_requirement：是否有明确等级要求
- proficiency_factor：熟练度满足度因子
- proficiency_is_weak：是否为弱维度

设计原则：
1. 所有已知别名必须正确映射（含同义词边界）
2. 空值/非法值不得被静默视为完全满足
3. 评分矩阵边界值正确（1/2/3 级 × 四等级）
4. weak 判定语义明确：有要求但未完全满足
"""

import pytest

from app.services.proficiency import (
    CANONICAL_PROFICIENCY_LEVELS,
    has_proficiency_requirement,
    normalize_proficiency_level,
    proficiency_factor,
    proficiency_is_weak,
)


class TestNormalizeProficiencyLevel:
    """等级归一化：所有已知别名 → 初级/中级/高级/专家。"""

    def test_canonical_levels_direct_match(self):
        """四个标准等级直接映射自身。"""
        assert normalize_proficiency_level("初级") == "初级"
        assert normalize_proficiency_level("中级") == "中级"
        assert normalize_proficiency_level("高级") == "高级"
        assert normalize_proficiency_level("专家") == "专家"

    def test_beginner_aliases(self):
        """初级别名：初等/入门/基础/了解/熟悉。"""
        for alias in ("初等", "入门", "基础", "了解", "熟悉"):
            assert normalize_proficiency_level(alias) == "初级", f"{alias} 应映射为初级"

    def test_intermediate_aliases(self):
        """中级别名：中等/掌握。"""
        for alias in ("中等", "掌握"):
            assert normalize_proficiency_level(alias) == "中级", f"{alias} 应映射为中级"

    def test_senior_aliases(self):
        """高级别名：资深/精通。"""
        for alias in ("资深", "精通"):
            assert normalize_proficiency_level(alias) == "高级", f"{alias} 应映射为高级"

    def test_whitespace_stripped(self):
        """前后空白字符应被剥离。"""
        assert normalize_proficiency_level("  高级  ") == "高级"
        assert normalize_proficiency_level("\t精通\n") == "高级"

    def test_unknown_level_returns_none(self):
        """未识别的等级返回 None，调用方必须显式处理。"""
        assert normalize_proficiency_level("首席") is None
        assert normalize_proficiency_level("架构师") is None
        assert normalize_proficiency_level("leader") is None

    def test_empty_string_returns_none(self):
        """空字符串不视为有效等级。"""
        assert normalize_proficiency_level("") is None
        assert normalize_proficiency_level("   ") is None

    def test_non_string_returns_none(self):
        """非字符串输入一律返回 None，不抛异常。"""
        assert normalize_proficiency_level(None) is None
        assert normalize_proficiency_level(3) is None
        assert normalize_proficiency_level(0) is None
        assert normalize_proficiency_level(True) is None
        assert normalize_proficiency_level([]) is None
        assert normalize_proficiency_level({}) is None

    def test_canonical_set_matches_four_levels(self):
        """标准等级集合恰好包含四个等级。"""
        assert CANONICAL_PROFICIENCY_LEVELS == {"初级", "中级", "高级", "专家"}


class TestHasProficiencyRequirement:
    """是否显式提供了熟练度字段（含无法识别的非法值）。"""

    def test_valid_level_has_requirement(self):
        """有效等级字符串 = 有要求。"""
        assert has_proficiency_requirement("高级") is True
        assert has_proficiency_requirement("初级") is True

    def test_unknown_level_still_has_requirement(self):
        """无法识别的非空等级也算有要求——不能静默视为无。"""
        assert has_proficiency_requirement("首席") is True
        assert has_proficiency_requirement("whatever") is True

    def test_none_has_no_requirement(self):
        """None = 无等级要求。"""
        assert has_proficiency_requirement(None) is False

    def test_empty_string_has_no_requirement(self):
        """空字符串 = 无等级要求。"""
        assert has_proficiency_requirement("") is False
        assert has_proficiency_requirement("   ") is False

    def test_non_string_has_no_requirement(self):
        """非字符串类型不视为有要求。"""
        assert has_proficiency_requirement(0) is False
        assert has_proficiency_requirement(3) is False
        assert has_proficiency_requirement(True) is False
        assert has_proficiency_requirement([]) is False


class TestProficiencyFactor:
    """熟练度满足度因子：按评分矩阵返回 0.0~1.0。"""

    def test_no_requirement_returns_full_score(self):
        """岗位无等级要求 → 不惩罚，因子 = 1.0。"""
        assert proficiency_factor(None, 1) == 1.0
        assert proficiency_factor("", 2) == 1.0
        assert proficiency_factor("   ", 3) == 1.0

    def test_no_candidate_level_returns_full_score(self):
        """候选人熟练度缺失 → 不惩罚，因子 = 1.0。"""
        assert proficiency_factor("高级", None) == 1.0

    def test_unknown_level_returns_zero(self):
        """非空但未识别的等级 → 0.0，防止被静默视为完全满足。"""
        assert proficiency_factor("首席", 3) == 0.0
        assert proficiency_factor("whatever", 1) == 0.0

    def test_beginner_matrix(self):
        """初级评分矩阵：{1: 0.85, 2: 1.0, 3: 1.0}。"""
        assert proficiency_factor("初级", 1) == pytest.approx(0.85)
        assert proficiency_factor("初级", 2) == pytest.approx(1.0)
        assert proficiency_factor("初级", 3) == pytest.approx(1.0)

    def test_intermediate_matrix(self):
        """中级评分矩阵：{1: 0.60, 2: 1.0, 3: 1.0}。"""
        assert proficiency_factor("中级", 1) == pytest.approx(0.60)
        assert proficiency_factor("中级", 2) == pytest.approx(1.0)
        assert proficiency_factor("中级", 3) == pytest.approx(1.0)

    def test_senior_matrix(self):
        """高级评分矩阵：{1: 0.30, 2: 0.60, 3: 1.0}。"""
        assert proficiency_factor("高级", 1) == pytest.approx(0.30)
        assert proficiency_factor("高级", 2) == pytest.approx(0.60)
        assert proficiency_factor("高级", 3) == pytest.approx(1.0)

    def test_expert_matrix(self):
        """专家评分矩阵：{1: 0.30, 2: 0.60, 3: 0.85}。"""
        assert proficiency_factor("专家", 1) == pytest.approx(0.30)
        assert proficiency_factor("专家", 2) == pytest.approx(0.60)
        assert proficiency_factor("专家", 3) == pytest.approx(0.85)

    def test_alias_same_as_canonical(self):
        """别名映射后的评分与标准等级一致。"""
        assert proficiency_factor("精通", 2) == proficiency_factor("高级", 2)
        assert proficiency_factor("入门", 1) == proficiency_factor("初级", 1)
        assert proficiency_factor("掌握", 3) == proficiency_factor("中级", 3)

    def test_candidate_level_out_of_range(self):
        """候选人等级不在 1/2/3 范围内 → 0.0。"""
        assert proficiency_factor("高级", 0) == pytest.approx(0.0)
        assert proficiency_factor("初级", 4) == pytest.approx(0.0)
        assert proficiency_factor("专家", -1) == pytest.approx(0.0)

    def test_factor_between_zero_and_one(self):
        """所有合法输入的因子都在 [0, 1] 区间内。"""
        for level in ("初级", "中级", "高级", "专家"):
            for candidate in (1, 2, 3):
                factor = proficiency_factor(level, candidate)
                assert 0.0 <= factor <= 1.0
                assert isinstance(factor, float)

    def test_monotonic_by_candidate_level(self):
        """同一岗位等级下，候选人等级越高，因子越高（单调非降）。"""
        for level in ("初级", "中级", "高级", "专家"):
            f1 = proficiency_factor(level, 1)
            f2 = proficiency_factor(level, 2)
            f3 = proficiency_factor(level, 3)
            assert f1 <= f2 <= f3, f"{level} 等级下因子应随候选人等级递增"


class TestProficiencyIsWeak:
    """弱维度判定：有等级要求但未完全满足。"""

    def test_no_requirement_not_weak(self):
        """无等级要求 → 不是弱维度。"""
        assert proficiency_is_weak(None, 1) is False
        assert proficiency_is_weak("", 2) is False

    def test_full_satisfaction_not_weak(self):
        """完全满足（factor == 1.0）→ 不是弱维度。"""
        assert proficiency_is_weak("初级", 2) is False
        assert proficiency_is_weak("中级", 3) is False
        assert proficiency_is_weak("高级", 3) is False

    def test_partial_satisfaction_is_weak(self):
        """部分满足（factor < 1.0）→ 是弱维度。"""
        assert proficiency_is_weak("高级", 1) is True
        assert proficiency_is_weak("专家", 2) is True
        assert proficiency_is_weak("中级", 1) is True

    def test_expert_level_3_is_weak(self):
        """专家等级即使候选人 3 级也只有 0.85，< 1.0 → 是弱维度。"""
        assert proficiency_is_weak("专家", 3) is True

    def test_unknown_level_is_weak(self):
        """无法识别的等级 factor=0.0 < 1.0 → 是弱维度。"""
        assert proficiency_is_weak("首席", 3) is True

    def test_no_candidate_not_weak(self):
        """候选人等级缺失 → 不惩罚 → 不是弱维度。"""
        assert proficiency_is_weak("高级", None) is False
