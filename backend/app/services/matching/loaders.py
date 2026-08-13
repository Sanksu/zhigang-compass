"""匹配领域：候选人画像构建 + 图谱岗位画像加载。

自 match.py 提取，供同步路由与 ARQ worker（match_recommend）共享，
避免 worker 反向依赖 API 路由模块。
"""

import time

from app.core.database import neo4j_driver
from app.services.matching.schemas import (
    CandidateProfile,
    CandidateProject,
    CandidateSkill,
    Necessity,
    PositionProfile,
    SkillRequirement,
)
from app.services.matching.semantic import SkillEmbedder

# 岗位画像进程级缓存：图谱结构变化低频，TTL 内复用，
# 避免每次匹配请求实时聚合（97 岗位 × ~15000 边）拖慢响应
_POSITIONS_CACHE_TTL = 300  # 秒
_positions_cache: dict = {"ts": 0.0, "positions": None}

# 岗位侧软技能并入 nice 时的权重（与聚合层 nice 两档中的低档一致）
_SOFT_SKILL_WEIGHT = 0.4


def build_candidate(parsed: dict) -> CandidateProfile:
    """从简历解析结果构建候选人画像。

    技能字段支持字符串列表或对象列表（resume_cache.parsed_data 两种形态兼容）。
    """
    skills: list[CandidateSkill] = []
    for s in parsed.get("skills", []):
        if isinstance(s, str):
            skills.append(CandidateSkill(skill_id=s, skill_name=s, proficiency=2))
        elif isinstance(s, dict):
            name = s.get("name", s.get("skill_id", ""))
            skills.append(CandidateSkill(
                skill_id=s.get("skill_id", name),
                skill_name=name,
                proficiency=int(s.get("proficiency", 2) or 2),
                low_confidence=bool(s.get("low_confidence", False)),
            ))

    projects: list[CandidateProject] = []
    for pr in parsed.get("projects", []):
        if isinstance(pr, str):
            projects.append(CandidateProject(name=pr))
        elif isinstance(pr, dict):
            projects.append(CandidateProject(
                name=pr.get("name", ""),
                stack=list(pr.get("stack", []) or []),
                description=pr.get("description", ""),
            ))

    certifications: list[str] = []
    for c in parsed.get("certifications", []):
        if isinstance(c, str):
            certifications.append(c)
        elif isinstance(c, dict) and c.get("name"):
            certifications.append(c["name"])

    return CandidateProfile(
        user_id=parsed.get("user_id", ""),
        skills=skills,
        total_years=float(parsed.get("total_years", 0) or 0),
        education_level=parsed.get("education_level"),
        domain_experience=parsed.get("domain_experience", []),
        projects=projects,
        certifications=certifications,
    )


def load_positions_from_graph() -> list[PositionProfile]:
    """从图谱聚合岗位画像（进程级 TTL 缓存；加载时批量预热技能向量）。

    语义向量缓存到 SkillEmbedder，评分阶段全部 cache hit，
    避免首次匹配请求触发全量 embedding 计算（>30s 前端超时的主因）。
    """
    now = time.monotonic()
    cached = _positions_cache.get("positions")
    if cached is not None and now - _positions_cache["ts"] < _POSITIONS_CACHE_TTL:
        return cached

    positions: dict[str, PositionProfile] = {}
    with neo4j_driver.session() as session:
        rows = session.run(
            """
            MATCH (p:Position)-[r:REQUIRES]->(s:Skill)
            RETURN p.id AS pid, p.name AS pname,
                   p.required_years AS req_years, p.last_updated AS last_updated,
                   p.industry AS industry,
                   s.id AS sid, s.name AS sname,
                   r.necessity AS necessity, r.weight AS weight,
                   r.level AS level, r.source_count AS source_count
            """
        )
        for rec in rows:
            pid = rec["pid"]
            pos = positions.get(pid)
            if pos is None:
                pos = PositionProfile(
                    position_id=pid,
                    name=rec.get("pname") or pid,
                    required_years=rec.get("req_years"),
                    last_updated=rec.get("last_updated"),
                    industry=rec.get("industry") or None,
                )
                positions[pid] = pos

            skill = SkillRequirement(
                skill_id=rec["sid"],
                skill_name=rec.get("sname") or rec["sid"],
                necessity=Necessity.MUST if rec.get("necessity") == "must" else Necessity.NICE,
                weight=float(rec.get("weight", 1.0) or 1.0),
                proficiency=rec.get("level"),
                source_count=int(rec.get("source_count", 1) or 1),
            )
            if skill.necessity == Necessity.MUST:
                pos.must_skills.append(skill)
            else:
                pos.nice_skills.append(skill)

        # 岗位侧软技能（Position.soft_skills，聚合层写回）：并入 nice 要求参与评分。
        # 候选人侧 LLM 推断软技能（low_confidence）命中时按 ×0.5 降权（engine._skill_similarity），
        # 与设计文档 9.2 节"LLM 推断兜底（标 low_confidence，匹配时降权 ×0.5）"一致。
        soft_rows = session.run(
            """
            MATCH (p:Position)
            RETURN p.id AS pid, p.soft_skills AS soft
            """
        )
        for rec in soft_rows:
            pos = positions.get(rec["pid"])
            if pos is None:
                continue
            soft = [s for s in (rec.get("soft") or []) if s]
            pos.soft_skills = soft
            for name in soft:
                pos.nice_skills.append(SkillRequirement(
                    skill_id=name,
                    skill_name=name,
                    necessity=Necessity.NICE,
                    weight=_SOFT_SKILL_WEIGHT,
                ))

    result = list(positions.values())
    # 预热语义向量：一次 batch encode 所有岗位技能名，评分时不再逐条前向推理
    SkillEmbedder.get().warm(
        [s.skill_name for p in result for s in (*p.must_skills, *p.nice_skills)]
    )
    _positions_cache["ts"] = now
    _positions_cache["positions"] = result
    return result
