"""聚合画像学历/薪资字段单元测试（08-29 画像补全）。

覆盖 parse_salary_range 实测语料形态与 build_aggregates 的学历/薪资收集。
"""

import pytest

from app.services.kg.aggregation import (
    PositionAgg,
    build_aggregates,
    parse_salary_range,
)
from types import SimpleNamespace


class TestParseSalaryRange:
    @pytest.mark.parametrize(
        "text, expected",
        [
            # 纯数字（元/月）
            ("8000-12000元", (8000, 12000)),
            ("15000-25000", (15000, 25000)),
            # 万（lo 无单位跟随 hi）
            ("1-1.5万·13薪", (10000, 15000)),
            ("2-4万", (20000, 40000)),
            ("1万-2万", (10000, 20000)),
            ("8千-1.2万", (8000, 12000)),
            # k
            ("15k-25k", (15000, 25000)),
            # 年折算月（÷12）
            ("10-20万/年", (8333, 16667)),
            # 天折算月（×22）
            ("200-300元/天", (4400, 6600)),
            # 美元（千分位剥除；美元≈1:1 保守处理，不乘汇率）
            ("$104,000-$150,000 annually", (104000, 150000)),
            ("$237,600 - $356,400", (237600, 356400)),
            # 时薪折算月（×176）
            ("$18.00 to $26.00/小时", (3168, 4576)),
        ],
    )
    def test_parses(self, text, expected):
        r = parse_salary_range(text)
        assert r is not None
        assert (round(r[0]), round(r[1])) == expected

    @pytest.mark.parametrize(
        "text",
        ["面议", None, "", "薪资优厚", "$60.90/hr-$82.30/hr"],  # 小数时薪噪声护栏
    )
    def test_unparsable_returns_none(self, text):
        assert parse_salary_range(text) is None


def _jd(pos: str, extraction: dict, source: str = "zhilian", crawled: str = "2026-08-28T10:00:00+08:00"):
    extraction = {"position_name": pos, **extraction}
    return SimpleNamespace(
        source=source,
        crawled_at=crawled,
        snapshot={"extraction": extraction, "normalized_position": pos},
    )


class TestAggregateEducationSalary:
    def _rows(self):
        return [
            _jd("Java开发工程师", {
                "education": {"level": "本科"},
                "salary_range": "15000-25000",
            }),
            _jd("Java开发工程师", {
                "education": {"level": "本科"},
                "salary_range": "18k-28k",
            }),
            _jd("Java开发工程师", {
                "education": {"level": "硕士"},
                "salary_range": "2-4万",
            }),
        ]

    def test_education_mode_and_salary_median_written(self):
        agg = build_aggregates(self._rows())
        pa = agg["Java开发工程师"]
        assert pa.education_levels.most_common(1)[0][0] == "本科"
        assert len(pa.salaries) == 3

    def test_anonymous_role_not_required_in_agg(self):
        """学历缺失的 JD 不计数（众数来自有值行）。"""
        rows = self._rows() + [
            _jd("Java开发工程师", {"education": {"level": None}, "salary_range": None}),
        ]
        agg = build_aggregates(rows)
        pa = agg["Java开发工程师"]
        assert pa.education_levels["本科"] == 2
        assert sum(pa.education_levels.values()) == 3  # None 不入 Counter
