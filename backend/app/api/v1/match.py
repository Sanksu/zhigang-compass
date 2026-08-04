"""匹配路由：自动推荐、人岗比对。

数据链路：resume_cache（候选人画像）→ Neo4j 图谱聚合岗位画像 → RuleBasedMatcher。
契约标注 recommend 为 202 异步，当前按设计文档 9.4 同步执行返回结果（M4 可迁异步）。
"""

import time
import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db, neo4j_driver
from app.models.business import ResumeCache
from app.schemas.common import ok, error
from app.services.matching.engine import RuleBasedMatcher
from app.services.matching.schemas import (
    CandidateProfile,
    CandidateProject,
    CandidateSkill,
    MatchMode,
    MatchRequest,
    Necessity,
    PositionProfile,
    SkillRequirement,
)
from app.services.matching.semantic import SkillEmbedder
from app.services.learning_path.generator import LearningPathGenerator

router = APIRouter()

# 岗位画像进程级缓存：图谱结构变化低频，TTL 内复用，
# 避免每次匹配请求实时聚合（97 岗位 × ~15000 边）拖慢响应
_POSITIONS_CACHE_TTL = 300  # 秒
_positions_cache: dict = {"ts": 0.0, "positions": None}


class RecommendRequest(BaseModel):
    resume_id: str
    top_n: int = Field(default=10, ge=1, le=50)


class CompareRequest(BaseModel):
    resume_id: str
    position_id: str


def _parse_resume_id(raw: str) -> str | None:
    """校验并规范化 resume_id（外部输入，非法 UUID 返回 None）。"""
    try:
        return str(uuid.UUID(raw))
    except (ValueError, AttributeError):
        return None


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

    return CandidateProfile(
        user_id=parsed.get("user_id", ""),
        skills=skills,
        total_years=float(parsed.get("total_years", 0) or 0),
        education_level=parsed.get("education_level"),
        domain_experience=parsed.get("domain_experience", []),
        projects=projects,
        certifications=parsed.get("certifications", []),
    )


def _load_positions_from_graph() -> list[PositionProfile]:
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

    result = list(positions.values())
    # 预热语义向量：一次 batch encode 所有岗位技能名，评分时不再逐条前向推理
    SkillEmbedder.get().warm(
        [s.skill_name for p in result for s in (*p.must_skills, *p.nice_skills)]
    )
    _positions_cache["ts"] = now
    _positions_cache["positions"] = result
    return result


def _load_evidence_for_position(position_id: str) -> list[dict]:
    """查询岗位技能链路的证据引用（Skill-MENTIONED_IN->Evidence 原始 JD）。

    图谱中每个技能关联若干原始 JD 证据（ev_xxxx），返回每条技能的
    代表性证据（每技能至多 3 条，总上限 20），供前端"证据引用"展示。
    置信度按技能支持度（REQUIRES.source_count 独立 JD 源数）归一化。
    """
    rows: list[dict] = []
    with neo4j_driver.session() as session:
        recs = session.run(
            """
            MATCH (p:Position {id: $pid})-[r:REQUIRES]->(s:Skill)-[:MENTIONED_IN]->(e:Evidence)
            RETURN s.name AS skill, e.source AS source, e.source_url AS url,
                   r.source_count AS source_count
            ORDER BY s.name
            """,
            pid=position_id,
        )
        seen: set[tuple] = set()
        per_skill: dict[str, int] = {}
        for rec in recs:
            skill = rec["skill"]
            if per_skill.get(skill, 0) >= 3 or len(rows) >= 20:
                continue
            key = (skill, rec["url"])
            if key in seen:
                continue
            seen.add(key)
            per_skill[skill] = per_skill.get(skill, 0) + 1
            # 置信度 = 技能独立 JD 源数归一化（5 个独立源视为满置信）
            cnt = float(rec.get("source_count") or 0)
            rows.append({
                "skill": skill,
                "source": rec["source"],
                "url": rec["url"],
                "confidence": round(min(cnt / 5, 1.0), 2),
            })
    return rows


@router.post("/recommend")
async def recommend(req: RecommendRequest, db: AsyncSession = Depends(get_db)):
    """自动推荐 Top-N 岗位（resume_cache → 匹配引擎）。"""
    resume_id = _parse_resume_id(req.resume_id)
    if resume_id is None:
        return error(400, "resume_id 格式非法")
    cache = await db.get(ResumeCache, resume_id)
    if cache is None:
        return error(404, "简历不存在")

    candidate = _build_candidate(cache.parsed_data)
    matcher = RuleBasedMatcher(
        _load_positions_from_graph(),
        semantic=SkillEmbedder.get(),
    )
    results = matcher.match(
        MatchRequest(candidate=candidate, mode=MatchMode.AUTO, top_n=req.top_n)
    )
    return ok(data={"items": [r.model_dump() for r in results]})


@router.post("/compare")
async def compare(req: CompareRequest, db: AsyncSession = Depends(get_db)):
    """人岗比对：单点同步比对（含差距三态 + 学习路径）。

    返回匹配结果 + gaps（missing/weak/matched 三态）+ learning_path
    （missing/weak 技能的先修链 + 课程 Top-3，设计文档 §9.5 / §4.6）。
    """
    resume_id = _parse_resume_id(req.resume_id)
    if resume_id is None:
        return error(400, "resume_id 格式非法")
    cache = await db.get(ResumeCache, resume_id)
    if cache is None:
        return error(404, "简历不存在")

    candidate = _build_candidate(cache.parsed_data)
    positions = _load_positions_from_graph()
    target = next((p for p in positions if p.position_id == req.position_id), None)
    if target is None:
        return error(404, "岗位不存在")

    semantic = SkillEmbedder.get()
    matcher = RuleBasedMatcher(positions, semantic=semantic)
    result = matcher.match(
        MatchRequest(
            candidate=candidate,
            mode=MatchMode.COMPARE,
            target_position_id=req.position_id,
        )
    )[0]

    path = await LearningPathGenerator().generate(candidate, target, semantic=semantic)
    return ok(
        data={
            **result.model_dump(),
            "gaps": [g.model_dump() for g in path.gaps],
            "learning_path": [item.model_dump() for item in path.items],
            "evidence_refs": _load_evidence_for_position(req.position_id),
        }
    )
