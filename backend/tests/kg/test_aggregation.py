"""岗位聚合 level 合并测试（设计文档 §5.5 REQUIRES 边）。

覆盖：_position_skills 携带 level、_most_common_level 众数、build_aggregates 收集 level。
"""

from types import SimpleNamespace

from app.services.kg.aggregation import (
    _most_common_level,
    _position_skills,
    build_aggregates,
    write_aggregates,
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


class _FakeSession:
    """write_aggregates 的会话桩：收集 run 调用参数。"""

    def __init__(self):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def run(self, query, **params):
        self.calls.append((query, params))
        return []


class TestSoftSkillsAggregation:
    """岗位侧软技能聚合（设计文档 9.2 节：soft_skills 白名单按 JD 命中计数）。"""

    def _row(self, position: str, source: str, soft: list[str]):
        return SimpleNamespace(
            snapshot={"extraction": {"position_name": position, "soft_skills": soft}},
            source=source,
        )

    def test_soft_skills_collected_with_whitelist_filter(self):
        rows = [
            self._row("Java开发工程师", "boss", ["团队协作", "沟通能力"]),
            self._row("Java开发工程师", "zhilian", ["团队协作"]),
            self._row("Java开发工程师", "lagou", ["体力好"]),  # 白名单外剔除
        ]
        agg = build_aggregates(rows)
        pa = agg["Java开发工程师"]
        assert dict(pa.soft_skills) == {"团队协作": 2, "沟通能力": 1}

    def test_write_aggregates_embeds_soft_skills(self):
        rows = [self._row("Java开发工程师", "boss", ["团队协作"])]
        agg = build_aggregates(rows)
        fake = _FakeSession()
        write_aggregates(fake, agg, now="2026-08-04T00:00:00")
        positions_items = fake.calls[0][1]["items"]
        assert positions_items[0]["soft_skills"] == ["团队协作"]
