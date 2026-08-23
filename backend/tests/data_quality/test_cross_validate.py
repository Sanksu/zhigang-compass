"""多平台交叉验证单元测试（DA-M3-03，设计文档 §4.5）。

覆盖薪资解析、岗位分组、技能一致性/verified、薪资异常、置信度三因子。
"""

import pytest

from app.services.data_quality.cross_validate import (
    build_position_groups,
    parse_monthly_salary,
    validate_group,
)
from app.services.data_quality.schemas import CrossValidationResult


def _rec(source: str, position: str, skills: list[str], salary: str = "", experience: str = "", location: str = "") -> dict:
    return {
        "source": source,
        "crawled_at": "2026-08-02T00:00:00+08:00",
        "snapshot": {
            "extraction": {
                "position_name": position,
                "skills": [{"name": s} for s in skills],
                "salary_range": salary,
            },
            "experience": experience,
            "location": location,
        },
    }


class TestParseMonthlySalary:
    @pytest.mark.parametrize("raw,expected", [
        ("50-80K", 65000.0),
        ("1.5-3万·14薪", 22500.0),
        ("200-220元/天", 210 * 21),
        ("$171,000.00 - $260,000.00 / year", (171000 + 260000) / 2 / 12 * 7),
        ("USD 175000.0-250000.0/年", (175000 + 250000) / 2 / 12 * 7),
        ("up to 170k", 170000.0),
        ("up to $120/hour", 120 * 8 * 21 * 7),
    ])
    def test_parse_formats(self, raw, expected):
        assert parse_monthly_salary(raw) == pytest.approx(expected)

    def test_unparseable_returns_none(self):
        assert parse_monthly_salary("") is None
        assert parse_monthly_salary("面议") is None
        assert parse_monthly_salary(None) is None


class TestBuildPositionGroups:
    def test_groups_by_normalized_name(self):
        records = [
            _rec("boss", "高级前端开发工程师", ["React"]),
            _rec("zhilian", "前端开发", ["Vue"]),
            _rec("boss", "算法工程师", ["机器学习"]),
        ]
        groups = build_position_groups(records)
        assert set(groups) == {"前端开发工程师", "算法工程师"}
        assert len(groups["前端开发工程师"]) == 2

    def test_generic_position_dropped(self):
        records = [_rec("boss", "技术", ["Python"])]  # 泛词归一化为空
        assert build_position_groups(records) == {}


class TestValidateGroup:
    def test_multi_source_verified(self):
        group = [
            _rec("boss", "Python开发工程师", ["Python", "Django"]),
            _rec("zhilian", "python", ["Python", "Django", "Redis"]),
            _rec("maimai", "python", ["Python"]),
        ]
        result = validate_group("Python开发工程师", group)
        assert result.source_count == 3
        assert result.verified is True
        # Python/Django 多源，Redis 单源（boss+zhilian 有 Django；Redis 仅 zhilian）
        assert result.verified_skill_ratio > 0
        assert "Redis" in result.unverified_skills

    def test_single_source_unverified_low_confidence(self):
        group = [_rec("boss", "前端开发", ["React", "Vue"])]
        result = validate_group("前端开发工程师", group)
        assert result.verified is False
        assert result.source_count == 1
        assert result.confidence < 0.6

    def test_salary_outlier(self):
        group = [
            _rec("boss", "Java开发工程师", ["Java"], salary="8-10K"),
            _rec("zhilian", "java", ["Java"], salary="25-30K"),
        ]
        result = validate_group("Java开发工程师", group)
        assert result.salary_outlier is True  # 9000 vs 27500 > 1.5 倍

    def test_salary_consistent(self):
        group = [
            _rec("boss", "Java开发工程师", ["Java"], salary="8-10K"),
            _rec("zhilian", "java", ["Java"], salary="9-11K"),
        ]
        result = validate_group("Java开发工程师", group)
        assert result.salary_outlier is False

    def test_experience_divergence(self):
        group = [
            _rec("boss", "Java开发工程师", ["Java"], experience="3-5年"),
            _rec("zhilian", "java", ["Java"], experience="3-5年"),
            _rec("maimai", "java", ["Java"], experience="经验不限"),
        ]
        result = validate_group("Java开发工程师", group)
        assert result.experience_divergence == pytest.approx(2 / 3, abs=0.01)  # round 后 0.667

    def test_model_shape(self):
        group = [_rec("boss", "运维工程师", ["Docker"])]
        result = validate_group("运维工程师", group)
        assert isinstance(result, CrossValidationResult)
        dumped = result.model_dump()
        assert dumped["position_name"] == "运维工程师"
        assert set(dumped) >= {"verified", "confidence", "unverified_skills"}


class TestCrossCitySalarySmoothing:
    """P12：薪资跨城市平滑——二线城市 JD 不再被一线城市 JD 误判为低薪异常。

    归一化口径：月薪 / 城市指数（北京 1.0 / 武汉 0.85 等）。跨城市正常薪资差
    平滑后 max/min 不再超阈值；同一城市口径下的真实分歧仍触发 salary_outlier。
    """

    def test_second_tier_jd_not_flagged_by_first_tier(self):
        # 原始口径：北京 25K vs 武汉 15K → max/min = 1.67 > 1.5 会被误判异常；
        # 城市归一化后：25.0 vs 15/0.85 ≈ 17.65 → 1.42 < 1.5，不再误报
        group = [
            _rec("boss", "Java开发工程师", ["Java"], salary="22-28K", location="北京·朝阳区"),
            _rec("zhilian", "java", ["Java"], salary="14-16K", location="武汉"),
        ]
        result = validate_group("Java开发工程师", group)
        assert result.salary_outlier is False
        assert "北京" in result.cities and "武汉" in result.cities

    def test_same_city_real_outlier_still_detected(self):
        # 同一城市（北京，指数相同）下薪资分歧 1.67 倍仍判异常
        group = [
            _rec("boss", "Java开发工程师", ["Java"], salary="22-28K", location="北京"),
            _rec("zhilian", "java", ["Java"], salary="14-16K", location="北京·海淀区"),
        ]
        result = validate_group("Java开发工程师", group)
        assert result.salary_outlier is True

    def test_location_parsing_from_separators(self):
        group = [
            _rec("boss", "前端开发工程师", ["React"], salary="22-28K", location="上海·浦东新区"),
            _rec("zhilian", "前端开发", ["React"], salary="18-22K", location="杭州 滨江区"),
        ]
        result = validate_group("前端开发工程师", group)
        assert "上海" in result.cities and "杭州" in result.cities

    def test_no_location_defaults_to_no_smoothing(self):
        # 无 location 时指数回退 1.0，行为与原实现一致（9K vs 27.5K 判异常）
        group = [
            _rec("boss", "Java开发工程师", ["Java"], salary="8-10K"),
            _rec("zhilian", "java", ["Java"], salary="25-30K"),
        ]
        result = validate_group("Java开发工程师", group)
        assert result.salary_outlier is True
        assert result.cities == []
