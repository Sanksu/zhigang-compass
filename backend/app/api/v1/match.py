"""匹配路由：自动推荐、人岗比对。

数据链路：resume_cache（候选人画像）→ Neo4j 图谱聚合岗位画像 → RuleBasedMatcher。
契约标注 recommend 为 202 异步，当前按设计文档 9.4 同步执行返回结果（M4 可迁异步）。
同步执行后结果持久化 Redis（TTL 24h）并返回 match_id，供 match/result|gap|path|feedback 查询。
"""

import asyncio
import json
import time
import uuid

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db, neo4j_driver, redis_client
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

# 岗位侧软技能并入 nice 时的权重（与聚合层 nice 两档中的低档一致）
_SOFT_SKILL_WEIGHT = 0.4


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


def _load_evidence_for_position(position_id: str) -> list[dict]:
    """查询岗位技能链路的证据引用（Skill-MENTIONED_IN->Evidence 原始 JD）。

    图谱中每个技能关联若干原始 JD 证据（ev_xxxx），返回每条技能的
    代表性证据（每技能至多 3 条，总上限 20），供前端"证据引用"展示。
    置信度 = 该技能证据量 / 归一化基数（8 条证据视为满置信 1.0），
    证据越多置信度越高（反映技能支持的跨源充分度）。
    """
    rows: list[dict] = []
    with neo4j_driver.session() as session:
        recs = session.run(
            """
            MATCH (p:Position {id: $pid})-[:REQUIRES]->(s:Skill)-[:MENTIONED_IN]->(e:Evidence)
            WITH s.name AS skill, collect(DISTINCT e) AS evs
            RETURN skill, size(evs) AS evidence_count,
                   [e IN evs | {source: e.source, source_url: e.source_url}] AS all_samples
            ORDER BY skill
            """,
            pid=position_id,
        )
        for rec in recs:
            if len(rows) >= 20:
                break
            count = rec["evidence_count"]
            confidence = round(min(count / 8.0, 1.0), 2)
            # 代表证据按源去重（每源至多 1 条），避免同源 JD 重复展示
            seen_sources: set[str] = set()
            for s in rec["all_samples"]:
                src = s["source"] or ""
                if src in seen_sources:
                    continue
                seen_sources.add(src)
                rows.append({
                    "skill": rec["skill"],
                    "source": src,
                    "url": s["source_url"],
                    "confidence": confidence,
                })
                if len(seen_sources) >= 3:
                    break
    return rows


# 匹配结果 Redis 持久化 TTL：24h（契约 M4 异步链路 result/gap/path/feedback 的存储底座）
_MATCH_RESULT_TTL = 60 * 60 * 24


def _ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


async def _persist_match_result(match_id: str, data: dict) -> None:
    """同步执行完成 → 写入结果快照 + 任务状态（同步即 success）。"""
    await redis_client.set(f"match:result:{match_id}", json.dumps(data), ex=_MATCH_RESULT_TTL)
    await redis_client.set(
        f"match:task:{match_id}",
        json.dumps({"match_id": match_id, "status": "success", "created_at": _ts()}),
        ex=_MATCH_RESULT_TTL,
    )


async def _load_match_result(match_id: str) -> dict | None:
    cached = await redis_client.get(f"match:result:{match_id}")
    return json.loads(cached) if cached else None


@router.post("/recommend")
async def recommend(req: RecommendRequest, db: AsyncSession = Depends(get_db)):
    """自动推荐 Top-N 岗位（resume_cache → 匹配引擎）。

    同步执行后将结果快照写入 Redis 并返回 match_id（供 match/result 等查询）。
    """
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
    match_id = str(uuid.uuid4())
    data = {"items": [r.model_dump() for r in results], "match_id": match_id}
    await _persist_match_result(match_id, data)
    return ok(data=data)


@router.post("/compare")
async def compare(req: CompareRequest, db: AsyncSession = Depends(get_db)):
    """人岗比对：单点同步比对（含差距三态 + 学习路径）。

    返回匹配结果 + gaps（missing/weak/matched 三态）+ learning_path
    （missing/weak 技能的先修链 + 课程 Top-3，设计文档 §9.5 / §4.6），
    并持久化快照返回 match_id（供 match/result|gap|path|feedback 查询）。
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
    match_id = str(uuid.uuid4())
    data = {
        "match_id": match_id,
        **result.model_dump(),
        "gaps": [g.model_dump() for g in path.gaps],
        "learning_path": [item.model_dump() for item in path.items],
        "evidence_refs": _load_evidence_for_position(req.position_id),
    }
    await _persist_match_result(match_id, data)
    return ok(data=data)


@router.get("/task/{task_id}")
async def match_task_status(task_id: str):
    """[M4] 查询推荐任务状态（同步执行已完成即返回 success）。"""
    cached = await redis_client.get(f"match:task:{task_id}")
    if cached is None:
        return error(404, "匹配任务不存在或已过期")
    return ok(data=json.loads(cached))


@router.get("/result/{match_id}")
async def match_result(match_id: str):
    """[M4] 获取匹配结果（recommend/compare 返回的 match_id）。"""
    data = await _load_match_result(match_id)
    if data is None:
        return error(404, "匹配结果不存在或已过期")
    return ok(data=data)


@router.get("/result/{match_id}/gap")
async def match_result_gap(match_id: str):
    """[M4] 获取差距分析（compare 结果的 gaps 三态列表）。"""
    data = await _load_match_result(match_id)
    if data is None:
        return error(404, "匹配结果不存在或已过期")
    return ok(data={"match_id": match_id, "gaps": data.get("gaps", [])})


@router.get("/result/{match_id}/path")
async def match_result_path(match_id: str):
    """[M4] 获取学习路径（compare 结果的 missing/weak 技能先修链 + 课程）。"""
    data = await _load_match_result(match_id)
    if data is None:
        return error(404, "匹配结果不存在或已过期")
    return ok(data={"match_id": match_id, "learning_path": data.get("learning_path", [])})


@router.get("/result/{match_id}/diagnosis")
async def match_diagnosis(match_id: str):
    """[M4] 获取人岗比对诊断报告（LLM 生成，结果缓存 24h）。

    以结果快照的分数/差距/学习路径/证据为 context 生成结构化报告
    （设计文档 §9.5：总体匹配度 + 雷达解读 + 关键差距 Top-5 + 路径解读 + 改进建议，
    每条差距断言附 evidence_id 可追溯）。仅人岗比对（compare）快照含 gaps，
    AUTO 推荐快照返回 400；LLM 不可用/超时返回 503（诊断是增强功能，不阻断主流程）。
    """
    data = await _load_match_result(match_id)
    if data is None:
        return error(404, "匹配结果不存在或已过期")
    if not data.get("gaps"):
        return error(400, "该匹配结果无差距数据，仅人岗比对可生成诊断报告")

    cached = await redis_client.get(f"match:diagnosis:{match_id}")
    if cached:
        return ok(data=json.loads(cached))

    from app.services.diagnosis.generator import generate_diagnosis
    from app.services.extraction.llm_provider import (
        LLMConfigurationError,
        LLMTimeoutError,
    )

    try:
        report = await asyncio.to_thread(generate_diagnosis, data)
    except (LLMConfigurationError, LLMTimeoutError) as e:
        return error(503, f"诊断报告生成失败：{e}")

    payload = {"match_id": match_id, **report.model_dump()}
    await redis_client.set(
        f"match:diagnosis:{match_id}", json.dumps(payload), ex=_MATCH_RESULT_TTL
    )
    return ok(data=payload)


class FeedbackRequest(BaseModel):
    match_id: str
    score: Literal[1, -1]


@router.post("/feedback")
async def match_feedback(req: FeedbackRequest):
    """[M4] 提交匹配反馈（1=👍 / -1=👎）。

    校验 match_id 结果存在后追加记录（保留 90 天，供后续匹配效果评估）。
    """
    cached = await _load_match_result(req.match_id)
    if cached is None:
        return error(404, "匹配结果不存在或已过期")
    key = f"match:feedback:{req.match_id}"
    await redis_client.rpush(key, json.dumps({"score": req.score, "created_at": _ts()}))
    await redis_client.expire(key, 90 * 24 * 3600)
    return ok(msg="反馈已记录")
