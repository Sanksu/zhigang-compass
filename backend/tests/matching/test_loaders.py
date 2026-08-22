"""匹配画像加载测试（loaders.py）。

覆盖：build_candidate 的字符串/字典双形态解析；load_positions_from_graph
的 must/nice 分组、软技能并入（weight 0.4）、TTL 缓存、向量预热。
Neo4j 与 SkillEmbedder 均 mock，不依赖真实服务。
"""

from __future__ import annotations

import time

import pytest

from app.services.matching.loaders import (
    _POSITIONS_CACHE_TTL,
    _SOFT_SKILL_WEIGHT,
    _positions_cache,
    build_candidate,
    load_positions_from_graph,
)
from app.services.matching.schemas import Necessity


# ── build_candidate ────────────────────────────────────────────────

def test_build_candidate_string_skills():
    """字符串技能列表 → 默认熟练度 2。"""
    cand = build_candidate({"skills": ["Python", "SQL"]})
    assert [s.skill_name for s in cand.skills] == ["Python", "SQL"]
    assert all(s.proficiency == 2 for s in cand.skills)
    assert all(s.skill_id == s.skill_name for s in cand.skills)


def test_build_candidate_dict_skills():
    """字典技能：name/skill_id/proficiency/low_confidence 解析。"""
    cand = build_candidate({
        "skills": [
            {"name": "Java", "proficiency": 3},
            {"skill_id": "go", "name": "Go", "proficiency": 1, "low_confidence": True},
        ]
    })
    java, go = cand.skills
    assert java.skill_name == "Java" and java.proficiency == 3 and not java.low_confidence
    assert go.skill_name == "Go" and go.proficiency == 1 and go.low_confidence


def test_build_candidate_mixed_skills():
    """字符串与字典混合技能。"""
    cand = build_candidate({"skills": ["Python", {"name": "Docker", "proficiency": 2}]})
    assert len(cand.skills) == 2


def test_build_candidate_projects():
    """projects 字符串/字典双形态。"""
    cand = build_candidate({
        "projects": [
            "订单系统",
            {"name": "推荐平台", "stack": ["Python", "Spark"], "description": "召回排序"},
        ]
    })
    p1, p2 = cand.projects
    assert p1.name == "订单系统" and p1.stack == []
    assert p2.name == "推荐平台" and p2.stack == ["Python", "Spark"]


def test_build_candidate_certifications():
    """certifications 字符串/字典双形态（字典取 name）。"""
    cand = build_candidate({
        "certifications": ["CISP", {"name": "AWS 认证"}]
    })
    assert cand.certifications == ["CISP", "AWS 认证"]


def test_build_candidate_empty_and_defaults():
    """空输入与缺省字段。"""
    cand = build_candidate({})
    assert cand.skills == [] and cand.projects == [] and cand.certifications == []
    assert cand.total_years == 0.0 and cand.user_id == ""


# ── load_positions_from_graph ──────────────────────────────────────

def _req_row(pid, pname, sid, sname, necessity, weight=0.8, level=None, sc=1, category=None):
    return {
        "pid": pid, "pname": pname, "req_years": 3, "last_updated": None,
        "industry": "互联网", "sid": sid, "sname": sname, "category": category,
        "necessity": necessity, "weight": weight, "level": level,
        "source_count": sc,
    }


class _FakeRows:
    """按查询内容路由的 Neo4j 行迭代器。"""

    def __init__(self, rows):
        self._rows = list(rows)
        self._i = 0

    def __iter__(self):
        return self

    def __next__(self):
        if self._i >= len(self._rows):
            raise StopIteration
        r = self._rows[self._i]
        self._i += 1
        return r


class _FakeSession:
    def __init__(self, req_rows, soft_rows):
        self._req_rows = req_rows
        self._soft_rows = soft_rows
        self.calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, query, **params):
        self.calls += 1
        if "soft_skills" in query:
            return _FakeRows(self._soft_rows)
        return _FakeRows(self._req_rows)


class _FakeDriver:
    def __init__(self, session):
        self._session = session

    def session(self):
        return self._session


class _FakeEmbedder:
    """SkillEmbedder 桩：记录 warm 调用，不加载真实模型。"""

    def __init__(self):
        self.warmed = []

    def warm(self, names):
        self.warmed.extend(names)


@pytest.fixture(autouse=True)
def _reset_cache():
    """每个测试前清空岗位缓存，避免跨测试污染。"""
    _positions_cache["ts"] = 0.0
    _positions_cache["positions"] = None
    yield
    _positions_cache["ts"] = 0.0
    _positions_cache["positions"] = None


def test_load_positions_must_nice_grouping(monkeypatch):
    """must/nice 技术栈正确分组；软技能走独立通道不进评分池。"""
    req_rows = [
        _req_row("p1", "后端工程师", "s1", "Java", "must", weight=0.9),
        _req_row("p1", "后端工程师", "s2", "Docker", "nice", weight=0.4),
    ]
    soft_rows = [{"pid": "p1", "soft": ["沟通能力"]}]
    fake = _FakeEmbedder()
    monkeypatch.setattr("app.services.matching.loaders.neo4j_driver",
                        _FakeDriver(_FakeSession(req_rows, soft_rows)))
    monkeypatch.setattr("app.services.matching.loaders.SkillEmbedder",
                        type("SE", (), {"get": staticmethod(lambda: fake)}))

    positions = load_positions_from_graph()
    assert len(positions) == 1
    pos = positions[0]
    assert pos.name == "后端工程师" and pos.industry == "互联网"
    assert [s.skill_name for s in pos.must_skills] == ["Java"]
    nice_names = [s.skill_name for s in pos.nice_skills]
    assert "Docker" in nice_names
    assert "沟通能力" not in nice_names  # 软技能不进 nice 评分池（独立通道）
    assert pos.soft_skills == ["沟通能力"]
    # 软技能在独立通道：展示权重 0.4、is_soft 打标
    soft_req = next(s for s in pos.soft_requirements if s.skill_name == "沟通能力")
    assert soft_req.weight == _SOFT_SKILL_WEIGHT
    assert soft_req.necessity == Necessity.NICE
    assert soft_req.is_soft is True
    # 预热包含全部技能名（含独立通道——差距分析对软技能做语义匹配）
    assert "Java" in fake.warmed and "沟通能力" in fake.warmed


def test_load_positions_ttl_cache(monkeypatch):
    """TTL 内第二次调用命中缓存，不重复查 Neo4j。"""
    req_rows = [_req_row("p1", "后端工程师", "s1", "Java", "must")]
    soft_rows = [{"pid": "p1", "soft": []}]
    fake_session = _FakeSession(req_rows, soft_rows)
    monkeypatch.setattr("app.services.matching.loaders.neo4j_driver",
                        _FakeDriver(fake_session))
    monkeypatch.setattr("app.services.matching.loaders.SkillEmbedder",
                        type("SE", (), {"get": staticmethod(lambda: _FakeEmbedder())}))

    load_positions_from_graph()
    calls_after_first = fake_session.calls
    load_positions_from_graph()
    assert fake_session.calls == calls_after_first  # 第二次命中缓存


def test_load_positions_cache_expiry(monkeypatch):
    """TTL 过期后重新查询。"""
    req_rows = [_req_row("p1", "后端工程师", "s1", "Java", "must")]
    soft_rows = [{"pid": "p1", "soft": []}]
    fake_session = _FakeSession(req_rows, soft_rows)
    monkeypatch.setattr("app.services.matching.loaders.neo4j_driver",
                        _FakeDriver(fake_session))
    monkeypatch.setattr("app.services.matching.loaders.SkillEmbedder",
                        type("SE", (), {"get": staticmethod(lambda: _FakeEmbedder())}))

    load_positions_from_graph()
    _positions_cache["ts"] = time.monotonic() - _POSITIONS_CACHE_TTL - 1
    load_positions_from_graph()
    assert fake_session.calls >= 2  # TTL 过期后重新查图


def test_load_positions_soft_skill_dedup(monkeypatch):
    """REQUIRES 软技能边与 Position.soft_skills 同名去重（独立通道内不重复）。"""
    req_rows = [
        _req_row("p1", "后端工程师", "s1", "团队协作", "nice", weight=0.4, category="软技能"),
    ]
    soft_rows = [{"pid": "p1", "soft": ["团队协作"]}]
    monkeypatch.setattr("app.services.matching.loaders.neo4j_driver",
                        _FakeDriver(_FakeSession(req_rows, soft_rows)))
    monkeypatch.setattr("app.services.matching.loaders.SkillEmbedder",
                        type("SE", (), {"get": staticmethod(lambda: _FakeEmbedder())}))

    pos = load_positions_from_graph()[0]
    # 独立通道内同名只出现一次（边版本带 skill_id/source_count 优先）
    soft_channel = [s.skill_name for s in pos.soft_requirements]
    assert soft_channel.count("团队协作") == 1
    # 评分池不含软技能
    assert not any(s.skill_name == "团队协作" for s in pos.nice_skills)


def test_load_positions_is_soft_tagging(monkeypatch):
    """软技能独立通道路由（2026-08-22 拍板：退出评分池）：
    - REQUIRES 边上 Skill.category=「软技能」→ 独立通道（must 标注也不例外）
    - Position.soft_skills 属性 → 独立通道（与边同名去重）
    - 技术类目技能照旧进 must/nice，is_soft=False
    """
    req_rows = [
        _req_row("p1", "算法工程师", "s1", "PyTorch", "must", category="AI·机器学习"),
        _req_row("p1", "算法工程师", "s2", "沟通能力", "nice", weight=0.4, category="软技能"),
        _req_row("p1", "算法工程师", "s3", "责任心", "must", weight=0.8, category="软技能"),
    ]
    soft_rows = [{"pid": "p1", "soft": ["责任心"]}]
    monkeypatch.setattr("app.services.matching.loaders.neo4j_driver",
                        _FakeDriver(_FakeSession(req_rows, soft_rows)))
    monkeypatch.setattr("app.services.matching.loaders.SkillEmbedder",
                        type("SE", (), {"get": staticmethod(lambda: _FakeEmbedder())}))

    pos = load_positions_from_graph()[0]
    # 评分池纯技术栈：must 仅 PyTorch（must 边软技能「责任心」也不计入），nice 为空
    assert [s.skill_name for s in pos.must_skills] == ["PyTorch"]
    assert pos.must_skills[0].is_soft is False
    assert pos.nice_skills == []
    # 独立通道：nice 边软技能 + must 边软技能 + 属性并入（同名去重）
    soft_channel = [s.skill_name for s in pos.soft_requirements]
    assert soft_channel == ["沟通能力", "责任心"]
    assert all(s.is_soft for s in pos.soft_requirements)


def test_load_positions_filters_edge_positions(monkeypatch):
    """岗位画像加载查询须剔除边缘岗位（freq<3 或 status=legacy，08-20 修复）。

    过滤器固化的 DB（Neo4j）侧，单元测试无法注入行来模拟，故此处断言
    发出的 REQUIRES 查询包含过滤谓词，防止后续改动误删把噪声岗位重新放回候选。
    """
    req_rows = [_req_row("p1", "后端工程师", "s1", "Java", "must")]
    soft_rows = [{"pid": "p1", "soft": []}]
    cap = _CapturingSession(req_rows, soft_rows)
    monkeypatch.setattr("app.services.matching.loaders.neo4j_driver",
                        _FakeDriver(cap))
    monkeypatch.setattr("app.services.matching.loaders.SkillEmbedder",
                        type("SE", (), {"get": staticmethod(lambda: _FakeEmbedder())}))

    load_positions_from_graph()
    primary = cap.primary_query
    assert "p.freq" in primary and "p.freq >= 3" in primary
    assert "coalesce(p.status" in primary and "'legacy'" in primary


class _CapturingSession(_FakeSession):
    """捕获主查询文本的会话桩。"""

    def __init__(self, req_rows, soft_rows):
        super().__init__(req_rows, soft_rows)
        self.primary_query = ""

    def run(self, query, **params):
        if "soft_skills" not in query:
            self.primary_query = query
        return super().run(query, **params)
