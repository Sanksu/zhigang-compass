"""多平台交叉验证单元测试（DA-M3-03，设计文档 §4.5）。

覆盖薪资解析、岗位分组、技能一致性/verified、薪资异常、置信度三因子。
"""

import pytest

from app.services.data_quality.cross_validate import (
    CONFIDENCE_GRAPH_MIN,
    EMERGING_CONFIDENCE_GRAPH_MIN,
    aggregation_gate_min,
    build_position_groups,
    filter_rows_for_aggregation,
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


class TestAggregationGate:
    """入图置信度门控（§4.5，H5 闭环：第二道防线由只算不拦改为门禁）。"""

    @staticmethod
    def _row(confidence, position_name="Java开发工程师"):
        from types import SimpleNamespace
        cv = (
            {"confidence": confidence, "position_name": position_name}
            if confidence is not None
            else None
        )
        snap = {"extraction": {"requirements": []}}
        if cv is not None:
            snap["cross_validation"] = cv
        return SimpleNamespace(snapshot=snap, source="boss")

    def test_single_source_low_confidence_blocked(self):
        """单源组（置信度上限 0.333 < 0.6）不参与聚合。"""
        rows = [self._row(0.333), self._row(0.333)]
        kept, stats = filter_rows_for_aggregation(rows, set())
        assert kept == []
        assert stats["blocked_jds"] == 2
        assert stats["blocked_positions"] == 1

    def test_multi_source_high_confidence_passes(self):
        """双源一致组（≥0.6）正常放行。"""
        rows = [self._row(0.787)]
        kept, stats = filter_rows_for_aggregation(rows, set())
        assert len(kept) == 1
        assert stats["blocked_jds"] == 0

    def test_emerging_position_uses_relaxed_threshold(self):
        """新兴岗位阈值下调至 0.5：置信度 0.55 的组既不被既有阈值拦、被新兴阈值放行。"""
        row_existing = self._row(0.55, position_name="Java开发工程师")
        row_emerging = self._row(0.55, position_name="大模型应用工程师")
        kept, _ = filter_rows_for_aggregation([row_existing, row_emerging], {"大模型应用工程师"})
        assert [r for r in kept] == [row_emerging]

    def test_emerging_gate_falls_back_to_snapshot_position(self):
        """历史行 cv 缺 position_name → 回退快照归一岗位判新兴阈值（§4.5）。

        0.55 ≥ 新兴 0.5 应放行（回退前一律按既有 0.6 拦截）；0.45 仍被新兴
        阈值拦截，证明兜底只补岗位名、不放松门控。
        """
        from types import SimpleNamespace

        from app.services.extraction.position_normalization import (
            POSITION_NORMALIZATION_VERSION,
        )

        def _legacy_row(confidence):
            snap = {
                "extraction": {"requirements": []},
                "normalized_position": "大模型应用工程师",
                "normalized_position_meta": {"version": POSITION_NORMALIZATION_VERSION},
                "cross_validation": {"confidence": confidence},
            }
            return SimpleNamespace(snapshot=snap, source="boss")

        kept, _ = filter_rows_for_aggregation([_legacy_row(0.55)], {"大模型应用工程师"})
        assert len(kept) == 1
        kept2, stats = filter_rows_for_aggregation([_legacy_row(0.45)], {"大模型应用工程师"})
        assert kept2 == []
        assert stats["blocked_jds"] == 1

    def test_unvalidated_rows_pass_with_count(self):
        """snapshot 无 cross_validation（历史数据）放行并计数，不静默拦截。"""
        rows = [self._row(None)]
        kept, stats = filter_rows_for_aggregation(rows, set())
        assert len(kept) == 1
        assert stats["unvalidated_jds"] == 1
        assert stats["blocked_jds"] == 0

    def test_gate_min_by_state(self):
        assert aggregation_gate_min("emerging") == EMERGING_CONFIDENCE_GRAPH_MIN == 0.5
        assert aggregation_gate_min("candidate") == 0.5
        assert aggregation_gate_min("stable") == CONFIDENCE_GRAPH_MIN == 0.6
        assert aggregation_gate_min(None) == 0.6
