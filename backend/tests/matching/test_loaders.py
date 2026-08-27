"""匹配画像加载测试（loaders.py）。

覆盖：build_candidate 的字符串/字典双形态解析（方案 A：岗位画像已改走
jd_raw 单条 JD，聚合加载器 _load_positions_uncached / load_positions_from_graph
已随聚合评分体系移除，本文件只保留候选画像构建测试）。
"""

from __future__ import annotations

from app.services.matching.loaders import build_candidate


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
