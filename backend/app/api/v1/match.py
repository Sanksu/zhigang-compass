"""匹配路由：自动推荐、人岗比对。

数据链路：resume_cache（候选人画像）→ Neo4j 图谱聚合岗位画像 → RuleBasedMatcher。
契约标注 recommend 为 202 异步，当前按设计文档 9.4 同步执行返回结果（M4 可迁异步）。
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db, get_neo4j
from app.models.business import ResumeCache
from app.schemas.common import ok, error
from app.services.matching.engine import RuleBasedMatcher
from app.services.matching.schemas import (
    CandidateProfile,
    CandidateSkill,
    MatchMode,
    MatchRequest,
    Necessity,
    PositionProfile,
    SkillRequirement,
)

router = APIRouter()


class RecommendRequest(BaseModel):
    resume_id: str
    top_n: int = Field(default=10, ge=1, le=50)


class CompareRequest(BaseModel):
    resume_id: str
    position_id: str


def _build_candidate(parsed: dict) -> CandidateProfile:
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

    return CandidateProfile(
        user_id=parsed.get("user_id", ""),
        skills=skills,
        total_years=float(parsed.get("total_years", 0) or 0),
        education_level=parsed.get("education_level"),
        domain_experience=parsed.get("domain_experience", []),
        projects=parsed.get("projects", []),
        certifications=parsed.get("certifications", []),
    )


def _load_positions_from_graph() -> list[PositionProfile]:
    """从图谱聚合岗位画像（轻量聚合层，M3 可由专用聚合任务替换）。"""
    positions: dict[str, PositionProfile] = {}
    with get_neo4j() as session:
        rows = session.run(
            """
            MATCH (p:Position)-[r:REQUIRES]->(s:Skill)
            RETURN p.id AS pid, p.name AS pname,
                   p.required_years AS req_years, p.last_updated AS last_updated,
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
    return list(positions.values())


@router.post("/recommend")
async def recommend(req: RecommendRequest, db: AsyncSession = Depends(get_db)):
    """自动推荐 Top-N 岗位（resume_cache → 匹配引擎）。"""
    cache = await db.get(ResumeCache, req.resume_id)
    if cache is None:
        return error(404, "简历不存在")

    candidate = _build_candidate(cache.parsed_data)
    matcher = RuleBasedMatcher(_load_positions_from_graph())
    results = matcher.match(
        MatchRequest(candidate=candidate, mode=MatchMode.AUTO, top_n=req.top_n)
    )
    return ok(data={"items": [r.model_dump() for r in results]})


@router.post("/compare")
async def compare(req: CompareRequest, db: AsyncSession = Depends(get_db)):
    """人岗比对：单点同步比对（含差距：matched_must / missing_must）。"""
    cache = await db.get(ResumeCache, req.resume_id)
    if cache is None:
        return error(404, "简历不存在")

    candidate = _build_candidate(cache.parsed_data)
    matcher = RuleBasedMatcher(_load_positions_from_graph())
    results = matcher.match(
        MatchRequest(
            candidate=candidate,
            mode=MatchMode.COMPARE,
            target_position_id=req.position_id,
        )
    )
    if not results:
        return error(404, "岗位不存在")
    return ok(data=results[0].model_dump())
