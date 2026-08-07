"""岗位聚合 level 合并测试（设计文档 §5.5 REQUIRES 边）。

覆盖：_position_skills 携带 level、_most_common_level 众数、build_aggregates 收集 level。
"""

from types import SimpleNamespace

from app.services.kg.aggregation import (
    _is_must,
    _most_common_level,
    _position_skills,
    build_aggregates,
    write_aggregates,
)


class TestMustJudgment:
    """must 判定（设计文档 §5.5）：jd_count≥3 按 must 标注覆盖率 >1/2；
    样本不足回退 must 标注占比 ≥1/2。"""

    def _sa(self, hit: int, must_count: int):
        return SimpleNamespace(hit=hit, must_count=must_count)

    def test_coverage_over_half_is_must(self):
        # jd_count=4，3 条 JD 标 must → 3/4 > 1/2 → must
        assert _is_must(self._sa(hit=4, must_count=3), jd_count=4) is True

    def test_coverage_exactly_half_is_nice(self):
        # jd_count=4，2 条标 must → 2/4 = 1/2，严格不大于 → nice
        assert _is_must(self._sa(hit=4, must_count=2), jd_count=4) is False

    def test_small_sample_fallback_to_must_ratio(self):
        # jd_count=2（样本不足）：must 标注占比 1/1 ≥ 1/2 → 按原逻辑 must
        assert _is_must(self._sa(hit=1, must_count=1), jd_count=2) is True

    def test_small_sample_under_half_is_nice(self):
        # jd_count=2：1 条 JD 出现技能但未标 must → 0/1 < 1/2 → nice
        assert _is_must(self._sa(hit=1, must_count=0), jd_count=2) is False


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
