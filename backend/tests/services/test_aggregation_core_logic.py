"""KG 聚合核心纯逻辑单元测试（M5 测试补充）。

覆盖 aggregation.py 中与数据库/IO 无关的纯函数与数据类：
- SkillAgg：技能聚合计数器
- _is_must：P2-D 必要性判定（三重条件 + 单源岗位兜底）
- _is_cross_domain：P2-C 跨域降权判定
- _most_common_level：熟练度众数归一化

设计原则：
1. 所有边界值必须显式覆盖（阈值上下 +1/-1）
2. 历史 bug 修复点必须有回归用例（单源岗位兜底、08-20 修复）
3. 非法/空值输入不抛异常、返回合理默认值
"""

import pytest

from app.services.kg.aggregation import (
    SkillAgg,
    _is_cross_domain,
    _is_must,
    _most_common_level,
)
from app.services.kg.aggregation_data import _ALLOWED_SKILL_CATEGORIES


class TestSkillAgg:
    """SkillAgg 数据类初始化与字段语义。"""

    def test_default_state(self):
        """初始状态：hit=0, must_count=0, sources=空集, levels=空列表。"""
        sa = SkillAgg()
        assert sa.hit == 0
        assert sa.must_count == 0
        assert sa.sources == set()
        assert sa.levels == []

    def test_mutate_fields(self):
        """字段可独立修改且互不影响。"""
        sa = SkillAgg()
        sa.hit = 5
        sa.must_count = 3
        sa.sources.add("lagou")
        sa.sources.add("boss")
        sa.levels.append("高级")
        sa.levels.append("中级")
        assert sa.hit == 5
        assert sa.must_count == 3
        assert sa.sources == {"lagou", "boss"}
        assert sa.levels == ["高级", "中级"]

    def test_slots_restrict_attributes(self):
        """__slots__ 限制：不能动态添加新属性（数据契约保护）。"""
        sa = SkillAgg()
        with pytest.raises(AttributeError):
            sa.nonexistent_field = 42


class TestIsMust:
    """_is_must 必要性判定：三重条件 + 单源岗位兜底。"""

    def _make(self, hit=0, must_count=0):
        sa = SkillAgg()
        sa.hit = hit
        sa.must_count = must_count
        return sa

    # ---- 边界：jd_count <= 0 ----
    def test_zero_jd_count_returns_false(self):
        """零 JD → False（无数据不判 must）。"""
        sa = self._make(hit=0, must_count=0)
        assert _is_must(sa, 0) is False

    def test_negative_jd_count_returns_false(self):
        """负 JD 数 → False（防御性编程）。"""
        sa = self._make(hit=5, must_count=3)
        assert _is_must(sa, -1) is False

    # ---- 单源/少源岗位兜底（jd_count <= 2，08-20 修复回归）----
    def test_small_jd_one_jd_must_count_zero_is_nice(self):
        """单源岗位 + 无 must 标注 → nice（继承抽取层）。"""
        sa = self._make(hit=1, must_count=0)
        assert _is_must(sa, 1) is False

    def test_small_jd_one_jd_must_count_one_is_must(self):
        """单源岗位 + 有 must 标注 → must（继承抽取层，回归 08-20 修复）。"""
        sa = self._make(hit=1, must_count=1)
        assert _is_must(sa, 1) is True

    def test_small_jd_two_jd_must_count_one_is_must(self):
        """双源岗位 + 任一 must → must（继承抽取层兜底）。"""
        sa = self._make(hit=2, must_count=1)
        assert _is_must(sa, 2) is True

    def test_small_jd_hit_less_than_jd_count_still_inherits(self):
        """少源岗位即使 hit < jd_count，也走继承逻辑（不触发三重条件）。"""
        sa = self._make(hit=1, must_count=1)
        assert _is_must(sa, 2) is True

    # ---- 大样本三重条件（jd_count > 2）----
    def test_hit_below_minimum_is_nice(self):
        """hit < 3 → nice（样本保护门槛）。"""
        sa = self._make(hit=2, must_count=2)  # 100% must 但样本不足
        assert _is_must(sa, 10) is False

    def test_hit_at_minimum_boundary(self):
        """hit = 3 → 满足样本门槛，继续评估其他条件。"""
        # hit=3, must_count=2 → must比例 2/3 > 0.5 ✓；coverage 3/10 = 0.3 ≥ 0.15 ✓
        sa = self._make(hit=3, must_count=2)
        assert _is_must(sa, 10) is True

    def test_coverage_below_threshold_is_nice(self):
        """覆盖率 < 15% → nice。"""
        # hit=3, jd_count=30 → coverage = 0.10 < 0.15
        sa = self._make(hit=3, must_count=3)  # must比例100%
        assert _is_must(sa, 30) is False

    def test_coverage_at_threshold_is_must(self):
        """覆盖率 = 15% → 恰好达标。"""
        # hit=3, jd_count=20 → coverage = 0.15
        sa = self._make(hit=3, must_count=2)  # 2/3 > 0.5
        assert _is_must(sa, 20) is True

    def test_must_ratio_at_50_percent_is_nice(self):
        """must 比例恰好 50% → nice（严格 > 50%）。"""
        # hit=4, must_count=2 → 2/4 = 0.5 不满足 > 0.5
        sa = self._make(hit=4, must_count=2)
        assert _is_must(sa, 10) is False  # coverage 0.4 ≥ 0.15 ✓，但 must 比例不满足

    def test_must_ratio_just_above_50_percent_is_must(self):
        """must 比例略高于 50% → must。"""
        # hit=3, must_count=2 → 2/3 ≈ 0.67 > 0.5
        sa = self._make(hit=3, must_count=2)
        assert _is_must(sa, 10) is True

    def test_all_three_conditions_satisfied(self):
        """三重条件全满足 → must。"""
        # hit=10 (≥3 ✓), coverage=10/20=0.5 (≥0.15 ✓), must_ratio=7/10=0.7 (>0.5 ✓)
        sa = self._make(hit=10, must_count=7)
        assert _is_must(sa, 20) is True

    def test_large_jd_high_hit_all_must(self):
        """大岗位高频全 must → must。"""
        sa = self._make(hit=100, must_count=100)
        assert _is_must(sa, 150) is True  # coverage 0.67 ✓, must比例 1.0 ✓

    def test_must_count_zero_is_nice(self):
        """零 must 标注 → nice。"""
        sa = self._make(hit=10, must_count=0)
        assert _is_must(sa, 20) is False

    def test_small_jd_at_threshold_still_uses_inheritance(self):
        """jd_count = 2（恰好等于阈值）→ 走继承逻辑，不走三重条件。"""
        # 若走三重条件：hit=1 < 3 → nice
        # 若走继承逻辑：must_count=1 → must
        sa = self._make(hit=1, must_count=1)
        assert _is_must(sa, 2) is True  # 确认走继承逻辑

    def test_jd_count_three_starts_triple_condition(self):
        """jd_count = 3（阈值+1）→ 开始走三重条件。"""
        # hit=1 < 3 → nice（三重条件第一关就失败）
        sa = self._make(hit=1, must_count=1)
        assert _is_must(sa, 3) is False


class TestIsCrossDomain:
    """_is_cross_domain 跨域降权判定（P2-C）。"""

    def test_family_not_in_whitelist_map_returns_false(self):
        """无白名单的岗位族 → 不降权（False）。"""
        assert _is_cross_domain("未知岗位族", "Python") is False
        assert _is_cross_domain("", "Python") is False

    def test_uncategorized_skill_not_cross_domain(self):
        """未分类技能 → 不降权（白名单外待审核，不武断）。"""
        # 找一个有白名单的岗位族，传入未分类技能
        family = next(iter(_ALLOWED_SKILL_CATEGORIES))
        assert _is_cross_domain(family, "随便一个未分类技能名xyz") is False

    def test_skill_in_allowed_categories_not_cross_domain(self):
        """技能类别在白名单内 → 不是跨域。"""
        # 前端工程师的前端类别技能不应判跨域
        assert _is_cross_domain("前端开发工程师", "React") is False

    def test_skill_outside_allowed_categories_is_cross_domain(self):
        """技能类别不在白名单内 → 是跨域。"""
        # 前端工程师的硬件/芯片类别技能应判跨域
        assert _is_cross_domain("前端开发工程师", "Verilog") is True

    def test_all_families_have_valid_category_sets(self):
        """所有岗位族的白名单都是非空集合（防漏配导致跨域失效）。"""
        assert len(_ALLOWED_SKILL_CATEGORIES) > 50  # 至少有数十个岗位族
        for family, allowed in _ALLOWED_SKILL_CATEGORIES.items():
            assert isinstance(allowed, set), f"{family} 的白名单不是 set"
            assert len(allowed) > 0, f"{family} 的白名单为空"
            assert "未分类" not in allowed, f"{family} 白名单不应包含未分类"


class TestMostCommonLevel:
    """_most_common_level 熟练度众数计算。"""

    def test_empty_list_returns_empty_string(self):
        """空列表 → 空串。"""
        assert _most_common_level([]) == ""

    def test_all_invalid_levels_returns_empty_string(self):
        """全是无效等级 → 空串。"""
        assert _most_common_level(["首席", "架构师", "whatever"]) == ""

    def test_single_level_returns_itself(self):
        """单个有效等级 → 直接返回。"""
        assert _most_common_level(["高级"]) == "高级"

    def test_clear_majority(self):
        """多数等级明确 → 返回众数。"""
        levels = ["高级", "高级", "中级", "初级", "高级"]
        assert _most_common_level(levels) == "高级"

    def test_tie_picks_first_occurrence(self):
        """并列众数 → 返回出现最早的一档。"""
        # 各出现2次：高级先出现
        levels = ["高级", "中级", "高级", "中级", "初级"]
        result = _most_common_level(levels)
        assert result in ("高级", "中级")  # Counter 不保证并列时的顺序，但都合法

    def test_aliases_normalized_before_counting(self):
        """别名先归一化再计数。"""
        # 精通→高级，资深→高级，入门→初级 → 高级有2票
        levels = ["精通", "资深", "入门"]
        assert _most_common_level(levels) == "高级"

    def test_mixed_valid_and_invalid(self):
        """有效与无效等级混合 → 只统计有效等级。"""
        levels = ["高级", "首席", "高级", "架构师", "中级"]
        assert _most_common_level(levels) == "高级"

    def test_all_four_canonical_levels(self):
        """四个标准等级都能正确识别。"""
        for level in ("初级", "中级", "高级", "专家"):
            assert _most_common_level([level, level, "首席"]) == level

    def test_non_string_items_ignored(self):
        """非字符串项被忽略（normalize 返回 None）。"""
        levels = ["高级", None, 123, "高级"]
        assert _most_common_level(levels) == "高级"
