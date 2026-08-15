"""管线口径一致性契约测试（08-15 全流程评估 P0-2）。

锁定历史上反复漂移的三类口径（实证：import/聚合/rebuild 三处不跳重复
#216、岗位状态双语义 #218、技能门控 P1-2）：
1. 岗位名归一化：聚合路径（build_aggregates）与抽取路径（normalize_position_name）
   对同一 extraction 产出同一岗位名
2. 重复记录跳过：build_aggregates 跳过 _duplicate_of（与 batch_extract/rebuild_graph 同口径）
3. 技能门控：聚合（_position_skills）与课程（filter_skill_tags）对同一技能名
   的规范化结果一致（canonical + 停用词/白名单）
"""

from types import SimpleNamespace

import pytest

from app.services.extraction.course_skills import filter_skill_tags
from app.services.extraction.dictionary import normalize_position_name
from app.services.kg.aggregation import _position_skills, build_aggregates


def _fake_row(snapshot: dict, source: str = "indeed", crawled_at: str = "2026-08-15T00:00:00+08:00"):
    """构造 build_aggregates 的最小 ORM 行。"""
    return SimpleNamespace(snapshot=snapshot, source=source, crawled_at=crawled_at)


class TestPositionNameConsistency:
    """契约 1：聚合与抽取路径岗位名一致（含 AI 泛词路由/映射/拦截）。"""

    @pytest.mark.parametrize(
        "position_name,skills",
        [
            ("Java开发工程师", ["Java", "Spring"]),
            ("AI应用", ["Python", "机器学习"]),
            ("AI 应用", ["Python", "机器学习"]),
            ("SDET", ["Python", "pytest"]),
            ("软件工程师", ["Python", "Django"]),
            ("GTM", ["销售"]),
            ("AI证据", ["Python"]),
            ("技术", None),
        ],
    )
    def test_aggregation_matches_normalize(self, position_name, skills):
        row = _fake_row({"extraction": {"position_name": position_name, "skills": [
            {"name": s} for s in (skills or [])
        ]}})
        agg = build_aggregates([row])
        expected = normalize_position_name(position_name, skills=skills or [])
        # 聚合输出岗位名集合（空岗位名不入 agg）
        assert set(agg.keys()) == ({expected} if expected else set())


class TestDuplicateSkipConsistency:
    """契约 2：重复记录跳过（聚合侧；与 batch_extract/rebuild_graph 同口径）。"""

    def test_duplicate_of_skipped_in_aggregation(self):
        row = _fake_row({
            "_duplicate_of": 12345,
            "extraction": {"position_name": "Java开发工程师", "skills": [{"name": "Java"}]},
        })
        agg = build_aggregates([row])
        assert "Java开发工程师" not in agg  # 重复记录不参与聚合

    def test_non_duplicate_included(self):
        row = _fake_row({
            "extraction": {"position_name": "Java开发工程师", "skills": [{"name": "Java"}]},
        })
        agg = build_aggregates([row])
        assert "Java开发工程师" in agg


class TestSkillGateConsistency:
    """契约 3：聚合与课程的技能门控（canonical + 停用词/白名单）一致。"""

    def test_skill_gate_matches_between_paths(self):
        skills = ["Python", "日志", "机器学习", "英语四六级", "Docker", "审批", "   "]
        # 聚合路径（_position_skills 输出规范化技能名集合）
        ext = {"requirements": [], "skills": [{"name": s} for s in skills]}
        agg_skills = {name for name, _, _ in _position_skills(ext)}
        # 课程路径（filter_skill_tags 输出规范化技能名集合）
        course_skills = set(filter_skill_tags(skills))
        # 两路径门控口径一致（停用词/白名单/去重）
        assert agg_skills == course_skills
        assert "日志" not in agg_skills and "英语四六级" not in agg_skills
        assert {"Python", "机器学习", "Docker"} <= agg_skills
