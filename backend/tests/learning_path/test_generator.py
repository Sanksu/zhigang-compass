"""学习路径生成器单元测试（AL-M4-03，设计文档 §9.5 / §4.6）。

课程加载器注入假实现，隔离图谱与 PostgreSQL 依赖。
"""

import pytest

from app.services.learning_path import prerequisites as mod
from app.services.learning_path.generator import LearningPathGenerator
from app.services.learning_path.schemas import CourseRecommendation, GapType
from app.services.matching.schemas import (
    CandidateProfile,
    CandidateSkill,
    Necessity,
    PositionProfile,
    SkillRequirement,
)


def _candidate(skills: list[tuple[str, int]]) -> CandidateProfile:
    return CandidateProfile(
        user_id="u1",
        skills=[
            CandidateSkill(skill_id=name, skill_name=name, proficiency=proficiency)
            for name, proficiency in skills
        ],
    )


def _req(name: str, necessity: Necessity = Necessity.MUST, weight: float = 1.0, proficiency=None):
    return SkillRequirement(
        skill_id=name, skill_name=name, necessity=necessity, weight=weight, proficiency=proficiency
    )


def _position(musts: list, nices: list | None = None) -> PositionProfile:
    return PositionProfile(position_id="p1", name="p1", must_skills=musts, nice_skills=nices or [])


class _FakeCourseLoader:
    """按技能名返回固定课程的假加载器（模拟质量分 Top-3）。"""

    def __init__(self, courses_by_skill: dict[str, list[CourseRecommendation]]):
        self.courses_by_skill = courses_by_skill

    async def __call__(
        self, skill_id: str, skill_name: str, top_k: int, semantic=None, sim_threshold=None
    ):
        return self.courses_by_skill.get(skill_name, [])[:top_k]


@pytest.fixture(autouse=True)
def _clear_prereq_cache():
    mod.load_prerequisite_config.cache_clear()
    yield
    mod.load_prerequisite_config.cache_clear()


@pytest.mark.asyncio
async def test_generate_missing_skill_with_chain_and_courses(monkeypatch):
    """缺失必备技能：输出先修链 + 课程 Top-3 + 学时 + 优先级。"""
    monkeypatch.setattr(
        mod,
        "load_prerequisite_config",
        lambda: {
            "default_hours_per_skill": 30.0,
            "skills": {
                "深度学习": {"prerequisites": ["机器学习", "Python"]},
                "机器学习": {"prerequisites": ["Python", "线性代数"]},
            },
        },
    )
    courses = [
        CourseRecommendation(course_id="c1", title="深度学习入门", platform="coursera", quality_score=0.9, recommended=True),
        CourseRecommendation(course_id="c2", title="神经网络实战", platform="coursera", quality_score=0.8, recommended=True),
        CourseRecommendation(course_id="c3", title="深度学习进阶", platform="icourse163", quality_score=0.7, recommended=True),
        CourseRecommendation(course_id="c4", title="低分课程", platform="icourse163", quality_score=0.4, recommended=False),
    ]
    loader = _FakeCourseLoader({"深度学习": courses})

    cand = _candidate([])
    pos = _position([_req("深度学习", weight=0.9)])
    result = await LearningPathGenerator(course_loader=loader).generate(cand, pos)

    assert len(result.gaps) == 1
    assert result.gaps[0].gap_type == GapType.MISSING
    assert len(result.items) == 1
    item = result.items[0]
    assert item.skill == "深度学习"
    assert set(item.prerequisites) == {"机器学习", "Python", "线性代数"}
    # 先修在前（拓扑序）
    assert item.prerequisites.index("机器学习") > item.prerequisites.index("线性代数")
    # 只取质量 Top-3（剔除低分课程）
    assert [c.course_id for c in item.courses] == ["c1", "c2", "c3"]
    # 学时 = 目标 + 先修链各技能基础学时（P1-2 分层：深度学习 70 + 机器学习 70
    # + Python 55 + 线性代数 40，白名单类别基准）
    assert item.estimated_hours == 235.0
    assert item.priority == "high"
    # 双轨制数据升级（task 1.2）：path 项均为待学 → status=doing，携带 demand/trend/roi
    assert item.status == "doing"
    assert item.demand is not None and item.roi is not None


@pytest.mark.asyncio
async def test_generate_only_missing_and_weak_skills(monkeypatch):
    """matched 技能不生成学习路径项。"""
    monkeypatch.setattr(mod, "load_prerequisite_config", lambda: {"default_hours_per_skill": 30.0, "skills": {}})
    cand = _candidate([("Python", 1), ("Go", 3)])  # Python 熟练度不足 → weak；Go 已匹配
    pos = _position(
        musts=[
            _req("Java"),  # missing
            _req("Python", proficiency="中级"),  # weak
            _req("Go"),  # matched
        ]
    )
    result = await LearningPathGenerator(course_loader=_FakeCourseLoader({})).generate(cand, pos)
    assert {i.skill for i in result.items} == {"Java", "Python"}
    assert len(result.gaps) == 3


@pytest.mark.asyncio
async def test_generate_no_gap_returns_empty_items(monkeypatch):
    monkeypatch.setattr(mod, "load_prerequisite_config", lambda: {"default_hours_per_skill": 30.0, "skills": {}})
    cand = _candidate([("Python", 2)])
    pos = _position([_req("Python")])
    result = await LearningPathGenerator(course_loader=_FakeCourseLoader({})).generate(cand, pos)
    assert result.items == []
    assert result.gaps[0].gap_type == GapType.MATCHED


@pytest.mark.asyncio
async def test_path_items_capped_at_top5(monkeypatch):
    """超过 5 个缺口技能时仅生成 Top-5 学习路径项（设计文档 §9.5）。"""
    monkeypatch.setattr(mod, "load_prerequisite_config", lambda: {"default_hours_per_skill": 30.0, "skills": {}})
    cand = _candidate([])
    musts = [_req(f"技能{i}") for i in range(7)]  # 7 个缺失必备技能
    result = await LearningPathGenerator(course_loader=_FakeCourseLoader({})).generate(cand, _position(musts))
    assert len(result.gaps) == 7
    assert len(result.items) == 5


@pytest.mark.asyncio
async def test_course_loader_receives_skill_id_and_top_k(monkeypatch):
    """课程加载器收到图谱 skill_id 与 top_k=3。"""
    monkeypatch.setattr(mod, "load_prerequisite_config", lambda: {"default_hours_per_skill": 30.0, "skills": {}})
    received = {}

    async def spy_loader(skill_id: str, skill_name: str, top_k: int, semantic=None, sim_threshold=None):
        received["skill_id"] = skill_id
        received["top_k"] = top_k
        return [CourseRecommendation(course_id="c1", title="t", platform="p")]

    cand = _candidate([])
    pos = _position([_req("Java")])
    await LearningPathGenerator(course_loader=spy_loader).generate(cand, pos)
    assert received == {"skill_id": "Java", "top_k": 3}
