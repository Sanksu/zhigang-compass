"""匹配路由：自动推荐、人岗比对。

数据链路：resume_cache（候选人画像）→ Neo4j 图谱聚合岗位画像 → RuleBasedMatcher。
契约（设计 §2.4.4）标注 recommend 为 202 异步 + task_id，当前同步执行返回结果，
M4（8/16）迁移；同步执行后结果持久化 Redis（TTL 24h）并返回 match_id，
供 match/result|gap|path|feedback 查询。匹配结果/反馈另按 §11.4.1 落 PostgreSQL
（match_results / match_feedback，Redis 为主存储，落库失败不阻断响应）。
"""

import asyncio
import json
import logging
import time
import uuid
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.common import owns_resume, parse_uuid, serialize_task
from app.api.deps import require_role
from app.core.arq_client import enqueue
from app.core.database import async_session_factory, get_db, neo4j_driver, redis_client
from app.core.errors import ERR_FORBIDDEN, ERR_INTERNAL, ERR_LLM_TIMEOUT, ERR_NOT_FOUND, ERR_VALIDATION
from app.models.business import (
    DiagnosisReportRecord,
    MatchFeedbackRecord,
    MatchResultRecord,
    ResumeCache,
    TaskStatus,
)
from app.schemas.common import error, ok
from app.services.embeddings.vector_store import PgvectorUnavailableError, load_project_vectors
from app.services.learning_path.generator import LearningPathGenerator
from app.services.matching.engine import RuleBasedMatcher
from app.services.matching.loaders import build_candidate, load_positions_from_graph
from app.services.matching.schemas import MatchMode, MatchRequest
from app.services.matching.semantic import SkillEmbedder

router = APIRouter()

logger = logging.getLogger(__name__)


class RecommendRequest(BaseModel):
    resume_id: str
    top_n: int = Field(default=10, ge=1, le=50)


class CompareRequest(BaseModel):
    resume_id: str
    position_id: str


def _load_evidence_for_position(position_id: str) -> list[dict]:
    """查询岗位技能链路的证据引用（Skill-EVIDENCED_BY->Evidence 原始 JD）。

    图谱中每个技能关联若干原始 JD 证据（ev_xxxx），返回每条技能的
    代表性证据（每技能至多 3 条，总上限 20），供前端"证据引用"展示。
    置信度 = 该技能证据量 / 归一化基数（8 条证据视为满置信 1.0），
    证据越多置信度越高（反映技能支持的跨源充分度）。
    """
    rows: list[dict] = []
    with neo4j_driver.session() as session:
        recs = session.run(
            """
            MATCH (p:Position {id: $pid})-[:REQUIRES]->(s:Skill)-[:EVIDENCED_BY]->(e:Evidence)
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


async def _load_match_result(match_id: str, user_id: str) -> dict | None:
    """加载匹配结果并校验归属（越权防护）。

    快照内 user_id（新写点）优先；存量快照无该字段时回退 match_results 表校验，
    防止本人访问修复前生成的结果被误拦。
    """
    cached = await redis_client.get(f"match:result:{match_id}")
    if cached is None:
        return None
    data = json.loads(cached)
    if data.get("user_id") == user_id:
        return data
    async with async_session_factory() as session:
        owner = await session.scalar(
            select(MatchResultRecord.id).where(
                MatchResultRecord.match_id == match_id,
                MatchResultRecord.user_id == user_id,
            )
        )
    return data if owner is not None else None


async def _load_or_404(match_id: str, user: dict) -> dict:
    """加载匹配结果并校验归属；不存在/越权返回 404 错误响应。

    越权与不存在同 404（不泄露资源存在性）；结果子路由共用。
    """
    data = await _load_match_result(match_id, user.get("sub", ""))
    if data is None:
        return error(ERR_NOT_FOUND, "匹配结果不存在或已过期", http_status=404)
    return data


async def _persist_diagnosis_report(
    session: AsyncSession, match_id: str, position_name: str, payload: dict
) -> None:
    """诊断报告幂等落库（match_id 唯一，重复生成更新而非追加）。"""
    row = await session.scalar(
        select(DiagnosisReportRecord).where(DiagnosisReportRecord.match_id == match_id)
    )
    if row is None:
        session.add(
            DiagnosisReportRecord(
                match_id=match_id, position_name=position_name, report=payload
            )
        )
    else:
        row.position_name = position_name
        row.report = payload
    await session.commit()


async def _persist_match_result_db(
    session: AsyncSession, match_id: str, position_name: str, user_id: str, data: dict
) -> None:
    """匹配结果幂等落库（§11.4.1 match_results：match_id 唯一，重复生成更新）。"""
    row = await session.scalar(
        select(MatchResultRecord).where(MatchResultRecord.match_id == match_id)
    )
    if row is None:
        session.add(
            MatchResultRecord(
                match_id=match_id, position_name=position_name, user_id=user_id, result=data
            )
        )
    else:
        row.position_name = position_name
        row.user_id = user_id
        row.result = data
    await session.commit()


async def _persist_feedback(
    session: AsyncSession, match_id: str, score: int, comment: str = ""
) -> None:
    """匹配反馈落库（§11.4.1 match_feedback；Redis List 为主存储，本表追加记录）。"""
    session.add(MatchFeedbackRecord(match_id=match_id, score=score, comment=comment))
    await session.commit()


async def _enqueue_match_recommend(
    resume_id: str, top_n: int, task_id: str, user_id: str
) -> None:
    """入队 ARQ match_recommend 任务。

    队列不可用时抛出异常由调用方处理（标记任务 failed），不静默吞错。
    """
    await enqueue(
        "match_recommend",
        resume_id=resume_id,
        top_n=top_n,
        task_id=task_id,
        user_id=user_id,
    )


@router.post("/recommend", status_code=202)
async def recommend(
    req: RecommendRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("user")),
):
    """自动推荐 Top-N 岗位（§2.4.4 契约：202 + task_id 异步）。

    创建 TaskStatus（match_recommend）入队 ARQ worker；任务完成后写入
    match:result（Redis TTL 24h）+ match_results 落库，前端轮询
    GET /match/task/{task_id} 拿到 match_id 后再取结果。
    """
    resume_id = parse_uuid(req.resume_id)
    if resume_id is None:
        return error(ERR_VALIDATION, "resume_id 格式非法")
    cache = await db.get(ResumeCache, resume_id)
    if cache is None:
        return error(ERR_NOT_FOUND, "简历不存在", http_status=404)
    if not await owns_resume(db, resume_id, user.get("sub", "")):
        return error(ERR_FORBIDDEN, "无权使用该简历发起匹配", http_status=403)

    task = TaskStatus(
        task_type="match_recommend",
        status="pending",
        result={
            "resume_id": resume_id,
            "top_n": req.top_n,
            "user_id": user.get("sub", ""),
        },
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)

    try:
        await _enqueue_match_recommend(
            resume_id, req.top_n, str(task.id), user.get("sub", "")
        )
    except Exception as e:
        task.status = "failed"
        task.error = "任务入队失败"  # 固定文案：详情仅入日志，防经 /match/task 透传内部信息
        await db.commit()
        logger.error(f"[match/recommend] 任务入队失败: task_id={task.id} err={e}")

    return ok(data={"task_id": task.id})


@router.post("/compare")
async def compare(
    req: CompareRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("user")),
):
    """人岗比对：单点同步比对（含差距三态 + 学习路径）。

    返回匹配结果 + gaps（missing/weak/matched 三态）+ learning_path
    （missing/weak 技能的先修链 + 课程 Top-3，设计文档 §9.5 / §4.6），
    并持久化快照返回 match_id（供 match/result|gap|path|feedback 查询）。
    """
    resume_id = parse_uuid(req.resume_id)
    if resume_id is None:
        return error(ERR_VALIDATION, "resume_id 格式非法")
    cache = await db.get(ResumeCache, resume_id)
    if cache is None:
        return error(ERR_NOT_FOUND, "简历不存在", http_status=404)
    if not await owns_resume(db, resume_id, user.get("sub", "")):
        return error(ERR_FORBIDDEN, "无权使用该简历发起比对", http_status=403)

    # 项目向量（pgvector project_embeddings 回填产物）：未回填/表不可用时为空 dict，
    # 评分回退文本相似度（engine._project_score 对空 dict 即回退）
    try:
        project_vectors = await load_project_vectors(db, resume_id)
    except PgvectorUnavailableError:
        logger.warning("project_embeddings 查询失败，项目比对回退文本相似度")
        project_vectors = {}

    def _compute():
        # 图谱加载 + SBERT 语义 + 规则匹配为 CPU/IO 密集，
        # 放线程池避免同步 Neo4j/SBERT 阻塞事件循环
        candidate = build_candidate(cache.parsed_data)
        positions = load_positions_from_graph()
        target = next((p for p in positions if p.position_id == req.position_id), None)
        if target is None:
            return None
        semantic = SkillEmbedder.get()
        matcher = RuleBasedMatcher(positions, semantic=semantic)
        result = matcher.match(
            MatchRequest(
                candidate=candidate,
                mode=MatchMode.COMPARE,
                target_position_id=req.position_id,
                project_vectors=project_vectors,
            )
        )[0]
        return candidate, target, result, semantic

    computed = await asyncio.to_thread(_compute)
    if computed is None:
        return error(ERR_NOT_FOUND, "岗位不存在", http_status=404)
    candidate, target, result, semantic = computed
    path = await LearningPathGenerator().generate(candidate, target, semantic=semantic)

    match_id = str(uuid.uuid4())
    data = {
        "match_id": match_id,
        "user_id": user.get("sub", ""),
        **result.model_dump(),
        "gaps": [g.model_dump() for g in path.gaps],
        "learning_path": [item.model_dump() for item in path.items],
        "evidence_refs": await asyncio.to_thread(_load_evidence_for_position, req.position_id),
    }
    await _persist_match_result(match_id, data)
    # 匹配结果落库（§11.4.1 match_results）：失败仅记日志，Redis 为主存储
    try:
        async with async_session_factory() as session:
            await _persist_match_result_db(
                session,
                match_id,
                position_name=target.name,
                user_id=user.get("sub") or "",
                data=data,
            )
    except Exception:
        logger.exception("匹配结果落库失败，跳过（不影响本次响应）")
    return ok(data=data)


async def _match_task_cache_owned(cached: dict, user_id: str) -> bool:
    """Redis match:task 回退分支归属校验（compare 同步结果无 user_id 字段）。

    该快照由 _persist_match_result 写入（键 match:task:{match_id}，内容
    {match_id, status, created_at}，不含 user_id），只能以其 match:result
    快照归属校验（_load_match_result 含越权防护）——防 UUID 可猜度下横向越权，
    与 TaskStatus 分支的归属校验口径一致；无 match_id 的快照按不属于任何人处理。
    """
    match_id = cached.get("match_id")
    if not match_id:
        return False
    return await _load_match_result(match_id, user_id) is not None


@router.get("/task/{task_id}")
async def match_task_status(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("user")),
):
    """查询推荐任务状态。

    优先查 TaskStatus（recommend 异步任务的真实状态：pending/running/success/failed），
    success 时附 match_id 供前端拉取结果；未命中则回退 Redis match:task 快照
    （compare 同步结果校验，兼容既有前端轮询语义）。
    仅返回当前用户发起的任务（TaskStatus.result.user_id），他人任务按不存在处理。
    """
    try:
        task_uuid = str(uuid.UUID(task_id))
    except (ValueError, AttributeError):
        return error(ERR_VALIDATION, "task_id 格式非法")
    task = await db.get(TaskStatus, task_uuid)
    if task is not None:
        if (task.result or {}).get("user_id") != user.get("sub", ""):
            return error(ERR_NOT_FOUND, "匹配任务不存在或已过期", http_status=404)
        data = serialize_task(task, exclude=("task_type", "result"))
        data["error"] = data["error"] or ""
        if task.status == "success":
            data["match_id"] = (task.result or {}).get("match_id")
        return ok(data=data)

    cached = await redis_client.get(f"match:task:{task_id}")
    if cached is None:
        return error(ERR_NOT_FOUND, "匹配任务不存在或已过期", http_status=404)
    data = json.loads(cached)
    if not await _match_task_cache_owned(data, user.get("sub", "")):
        return error(ERR_NOT_FOUND, "匹配任务不存在或已过期", http_status=404)
    return ok(data=data)


@router.get("/result/{match_id}")
async def match_result(match_id: str, user: dict = Depends(require_role("user"))):
    """[M4] 获取匹配结果（recommend/compare 返回的 match_id，仅限本人结果）。"""
    data = await _load_or_404(match_id, user)
    return ok(data=data)


@router.get("/result/{match_id}/gap")
async def match_result_gap(match_id: str, user: dict = Depends(require_role("user"))):
    """[M4] 获取差距分析（compare 结果的 gaps 三态列表，仅限本人结果）。"""
    data = await _load_or_404(match_id, user)
    return ok(data={"match_id": match_id, "gaps": data.get("gaps", [])})


@router.get("/result/{match_id}/path")
async def match_result_path(match_id: str, user: dict = Depends(require_role("user"))):
    """[M4] 获取学习路径（compare 结果的 missing/weak 技能先修链 + 课程，仅限本人结果）。"""
    data = await _load_or_404(match_id, user)
    return ok(data={"match_id": match_id, "learning_path": data.get("learning_path", [])})


@router.get("/result/{match_id}/diagnosis")
async def match_diagnosis(match_id: str, user: dict = Depends(require_role("user"))):
    """[M4] 获取人岗比对诊断报告（LLM 生成，结果缓存 24h + 落库）。

    以结果快照的分数/差距/学习路径/证据为 context，并动态检索图谱上下文
    （§6.4 通用 RAG：岗位定义 + 技能 + 历史诊断报告）生成结构化报告
    （设计文档 §9.5：总体匹配度 + 雷达解读 + 关键差距 Top-5 + 路径解读 + 改进建议，
    每条差距断言附 evidence_id 可追溯）。仅人岗比对（compare）快照含 gaps，
    AUTO 推荐快照返回 400；LLM 超时返回 5003/504、配置不可用返回 503（诊断是增强功能，不阻断主流程）。
    """
    data = await _load_or_404(match_id, user)
    if not data.get("gaps"):
        return error(ERR_VALIDATION, "该匹配结果无差距数据，仅人岗比对可生成诊断报告")

    cached = await redis_client.get(f"match:diagnosis:{match_id}")
    if cached:
        return ok(data=json.loads(cached))

    from app.services.diagnosis.generator import generate_diagnosis
    from app.services.extraction.llm_provider import (
        LLMConfigurationError,
        LLMExtractionError,
        LLMTimeoutError,
    )

    # 动态检索图谱上下文（§6.4）：岗位定义/技能/历史诊断报告，失败降级为空上下文
    rag_chunks: list[dict] = []
    position_name = data.get("position_name", "")
    if position_name:
        from app.services.matching.semantic import SemanticUnavailableError, SkillEmbedder
        from app.services.rag.retrieval import retrieve_context

        try:
            embedder = SkillEmbedder.get()
        except SemanticUnavailableError:
            # 语义模型不可用 → 降级为关键词路检索，不阻塞诊断
            embedder = None
        try:
            async with async_session_factory() as session:
                chunks = await retrieve_context(
                    position_name, session, neo4j=neo4j_driver, embedder=embedder
                )
            rag_chunks = [c.__dict__ for c in chunks]
        except Exception:
            # RAG 是增强：检索失败不阻塞诊断生成（§6.4 证据不足时明确说明）
            logger.exception("图谱上下文检索失败，诊断降级为无 RAG 上下文")

    try:
        report = await asyncio.to_thread(
            generate_diagnosis, data, rag_chunks=rag_chunks
        )
    except LLMTimeoutError as e:
        # 契约 5003（§2.4.7）：LLM 调用超时 → 504，前端可据此触发降级链
        logger.warning("诊断报告 LLM 超时: %s", e)
        return error(ERR_LLM_TIMEOUT, "诊断报告生成失败：LLM 调用超时", http_status=504)
    except LLMConfigurationError as e:
        # 配置不可用（无 api_key/全部禁用）→ 503（契约：LLM 不可用或超时）
        logger.warning("诊断报告 LLM 配置不可用: %s", e)
        return error(ERR_INTERNAL, "诊断报告生成失败：LLM 配置不可用", http_status=503)
    except LLMExtractionError as e:
        # 全部 provider 失败（超时/连接/校验）→ 503，避免裸 500 且与契约一致
        logger.warning("诊断报告 LLM 抽取失败: %s", e)
        return error(ERR_INTERNAL, "诊断报告生成失败：LLM 服务异常", http_status=503)

    payload = {"match_id": match_id, **report.model_dump()}
    await redis_client.set(
        f"match:diagnosis:{match_id}", json.dumps(payload), ex=_MATCH_RESULT_TTL
    )
    # 诊断报告落库（§6.4 RAG 检索源之一）：失败不影响响应（Redis 为主存储）
    try:
        async with async_session_factory() as session:
            await _persist_diagnosis_report(session, match_id, position_name, payload)
    except Exception:
        logger.exception("诊断报告落库失败，跳过（不影响本次响应）")
    return ok(data=payload)


class FeedbackRequest(BaseModel):
    match_id: str
    score: Literal[1, -1]


@router.post("/feedback")
async def match_feedback(
    req: FeedbackRequest,
    user: dict = Depends(require_role("user")),
):
    """[M4] 提交匹配反馈（1=👍 / -1=👎）。

    校验 match_id 结果存在（归属）后追加记录（保留 90 天，供后续匹配效果评估）。
    """
    cached = await _load_match_result(req.match_id, user.get("sub", ""))
    if cached is None:
        return error(ERR_NOT_FOUND, "匹配结果不存在或已过期", http_status=404)
    key = f"match:feedback:{req.match_id}"
    await redis_client.rpush(key, json.dumps({"score": req.score, "created_at": _ts()}))
    await redis_client.expire(key, 90 * 24 * 3600)
    # 反馈落库（§11.4.1 match_feedback）：失败仅记日志，Redis 为主存储
    try:
        async with async_session_factory() as session:
            await _persist_feedback(session, req.match_id, req.score)
    except Exception:
        logger.exception("反馈落库失败，跳过（不影响本次响应）")
    return ok(msg="反馈已记录")
