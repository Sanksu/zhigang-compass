"""岗位聚合 level 合并测试（设计文档 §5.5 REQUIRES 边）。

覆盖：_position_skills 携带 level、_most_common_level 众数、build_aggregates 收集 level。
"""

from types import SimpleNamespace

from app.services.kg.aggregation import (
    _most_common_level,
    _position_skills,
    build_aggregates,
)


class TestPositionSkills:
    def test_requirements_carry_level(self):
        ext = {
            "requirements": [
                {"skill_name": "Java", "necessity": "must", "level": "高级"},
                {"skill_name": "MySQL", "necessity": "must", "level": None},
            ]
        }
        assert _position_skills(ext) == [
            ("Java", "must", "高级"),
            ("MySQL", "must", ""),
        ]

    def test_fallback_to_skills_without_level(self):
        ext = {"skills": [{"name": "Python"}, {"name": "Go"}]}
        assert _position_skills(ext) == [("Python", "nice", ""), ("Go", "nice", "")]


class TestMostCommonLevel:
    def test_empty_returns_empty(self):
        assert _most_common_level([]) == ""

    def test_majority_wins(self):
        assert _most_common_level(["初级", "中级", "中级"]) == "中级"

    def test_tie_keeps_first_seen(self):
        assert _most_common_level(["高级", "初级", "初级", "高级"]) == "高级"


class TestBuildAggregatesLevel:
    def _row(self, position: str, source: str, level: str | None):
        return SimpleNamespace(
            snapshot={
                "extraction": {
                    "position_name": position,
                    "requirements": [
                        {"skill_name": "Java", "necessity": "must", "level": level},
                    ],
                }
            },
            source=source,
        )

    def test_levels_collected_per_skill(self):
        rows = [
            self._row("Java开发工程师", "boss", "高级"),
            self._row("Java开发工程师", "zhilian", "中级"),
            self._row("Java开发工程师", "zhilian", None),  # 无 level 不计入
        ]
        agg = build_aggregates(rows)
        pa = agg["Java开发工程师"]
        assert pa.skills["Java"].levels == ["高级", "中级"]
        assert pa.skills["Java"].hit == 3
