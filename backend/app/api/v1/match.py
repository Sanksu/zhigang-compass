"""匹配路由：自动推荐、人岗比对。

数据链路：resume_cache（候选人画像）→ jd_raw 单条 JD 画像 → 评分引擎。
契约（设计 §2.4.4）标注 recommend 为 202 异步 + task_id，当前同步执行返回结果，
M4（8/16）迁移；同步执行后结果持久化 Redis（TTL 24h）并返回 match_id，
供 match/result|gap|path|feedback 查询。匹配结果/反馈另按 §11.4.1 落 PostgreSQL
（match_results / match_feedback，Redis 为主存储，落库失败不阻断响应）。
"""

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
from app.core.database import (
    async_neo4j_driver,
    async_session_factory,
    get_db,
    redis_client,
)
from app.core.errors import ERR_FORBIDDEN, ERR_NOT_FOUND, ERR_VALIDATION
from app.models.business import (
    DiagnosisReportRecord,
    MatchFeedbackRecord,
    MatchResultRecord,
    ResumeCache,
    TaskStatus,
)
from app.models.raw import JDRaw
from app.schemas.common import error, ok
from app.services.embeddings.vector_store import PgvectorUnavailableError, load_project_vectors
from app.services.learning_path.generator import LearningPathGenerator
from app.services.matching.jd_match import (
    load_jd_evidence_refs,
    score_jd_compare,
    score_jd_one,
)
from app.services.matching.loaders import build_candidate
from app.services.matching.weights import load_weights
from app.services.matching.semantic import SkillEmbedder

router = APIRouter()

logger = logging.getLogger(__name__)


class RecommendRequest(BaseModel):
    resume_id: str
    top_n: int = Field(default=10, ge=1, le=50)


class CompareRequest(BaseModel):
    resume_id: str
    position_id: str


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

    Redis 快照优先；过期/丢失后回退 match_results 耐久副本并回填缓存
    （08-23 闭环收敛：修复双写单读——PG 镜像此前只写不可恢复）。快照内
    user_id（新写点）优先；存量快照无该字段时回退表级归属校验，防止
    本人访问修复前生成的结果被误拦。
    """
    cached = await redis_client.get(f"match:result:{match_id}")
    if cached is not None:
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

    async with async_session_factory() as session:
        row = await session.scalar(
            select(MatchResultRecord).where(
                MatchResultRecord.match_id == match_id,
                MatchResultRecord.user_id == user_id,
            )
        )
    if row is None or not isinstance(row.result, dict) or not row.result:
        return None
    try:
        await redis_client.set(
            f"match:result:{match_id}",
            json.dumps(row.result, ensure_ascii=False),
            ex=_MATCH_RESULT_TTL,
        )
    except Exception:
        logger.warning("匹配结果缓存回填失败（不影响本次响应）", exc_info=True)
    return row.result


async def _load_or_404(match_id: str, user: dict) -> dict:
    """加载匹配结果并校验归属；不存在/越权返回 404 错误响应。

    越权与不存在同 404（不泄露资源存在性）；结果子路由共用。
    """
    data = await _load_match_result(match_id, user.get("sub", ""))
    if data is None:
        return error(ERR_NOT_FOUND, "匹配结果不存在或已过期", http_status=404)
    return data


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

    # 方案 A：compare 统一到 JD 级评分。岗位画像来自 jd_raw 单条 JD（非图谱聚合），
    # 加载该岗位名下全部 JD 评分后取真正最高分一条（score_jd_compare）。推荐列表
    # position_id=岗位名（normalized_position），调用方直接按岗位名加载。
    position_name = req.position_id.strip()

    async def _compute():
        # 构建候选人 + JD 级匹配为 IO 密集（DB 查询）放事件循环；评分纯计算段
        # score_jd_compare 内部已 to_thread，无需在此再包线程
        candidate = build_candidate(cache.parsed_data)
        semantic = SkillEmbedder.get()
        found = await score_jd_compare(
            db, candidate, position_name, project_vectors, semantic=semantic,
        )
        if found is None:
            return None
        target, result, scored_items, jd_compared = found
        return candidate, target, result, scored_items, jd_compared, semantic

    computed = await _compute()
    if computed is None:
        return error(ERR_NOT_FOUND, "岗位不存在", http_status=404)
    candidate, target, result, scored_items, jd_compared, semantic = computed
    path = await LearningPathGenerator().generate(candidate, target, semantic=semantic)

    # 岗位域归属（2026-09-01 域治理成果接入）：图谱 Position.domain_name 按
    # 岗位名取域徽标；无域（弃权池/未同步）为 null，前端不渲染
    domain_name = None
    try:
        async with async_neo4j_driver.session() as session:
            record = await session.run(
                "MATCH (p:Position {name: $name}) RETURN p.domain_name AS d",
                name=position_name,
            )
            row = await record.single()
            domain_name = row["d"] if row is not None else None
    except Exception:
        logger.warning("[match/compare] 域归属查询失败，忽略")

    # JD 原文（compare 详情溯源）：score_jd_compare 以 str(row.id) 构建
    # PositionProfile.position_id，按主键回读最佳 JD 行的 raw_text；正文截断
    # 8000 字符防超大 payload，行已删除/主键异常时置 null 不阻断比对
    jd_original = None
    try:
        jd_row = await db.get(JDRaw, int(target.position_id))
    except (TypeError, ValueError):
        jd_row = None
    if jd_row is not None:
        jd_original = {
            "jd_title": str((jd_row.snapshot or {}).get("title") or target.name).strip(),
            "source": jd_row.source or "",
            "source_url": jd_row.source_url or "",
            "text": (jd_row.raw_text or "").strip()[:8000],
            # 评分溯源（2026-09-01）：result.total_score 即最佳 JD 的得分
            "score": round(result.total_score, 4),
        }

    # 实际评分权重（BT v3）透出：前端三维分解展示真实口径（此前前端硬编码
    # 0.6/0.2/0.2 与配置不符）
    w_must, w_nice, w_exp = load_weights()

    # 该岗位下全部已评分 JD 排名（前端下拉逐条查看各 JD 详情；scored_items 已按分降序）
    jd_breakdown = [
        {
            "jd_id": prof.position_id,
            "jd_title": res.position_name,
            "total_score": round(res.total_score, 4),
            "must_score": res.must_score,
            "nice_score": round(res.nice_score, 4),
            "exp_score": round(res.exp_score, 4),
            "hit_count": len(res.matched_must) + len(res.matched_nice),
        }
        for prof, res in scored_items
    ]

    match_id = str(uuid.uuid4())
    data = {
        "match_id": match_id,
        "user_id": user.get("sub", ""),
        # 用于下拉切换单 JD 详情时重建候选人（/match/result/{match_id}/jd/{jd_id}）
        "resume_id": resume_id,
        **result.model_dump(),
        # 对外 position_id / position_name 用岗位名（与推荐列表 JD 候选模式一致，
        # 见 jd_aggregate position_id=岗位名），避免前端显示/比较不一致
        "position_id": position_name,
        # 岗位域徽标（域治理成果接入；无域为 null）
        "domain_name": domain_name,
        # 实际评分权重（BT v3），前端展示真实口径
        "weights": {"must": round(w_must, 4), "nice": round(w_nice, 4), "exp": round(w_exp, 4)},
        # JD 级评分溯源：total_score 为同岗 jd_compared 条 JD 中的最高分
        "jd_compared": jd_compared,
        # 同岗下各 JD 评分排名（下拉切换）
        "jd_breakdown": jd_breakdown,
        "gaps": [g.model_dump() for g in path.gaps],
        "learning_path": [item.model_dump() for item in path.items],
        "learning_path_blocked": path.blocked,
        "learning_path_block_reason": path.block_reason,
        # 证据引用（方案 A：岗位名下 jd_raw 技能 → 采集源，替代旧 Neo4j 图谱链路）
        "evidence_refs": await load_jd_evidence_refs(db, position_name),
        # 最佳匹配 JD 原文（随快照持久化，GET /match/result 重载同样可见）
        "jd_original": jd_original,
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


async def _compose_jd_detail(
    db: AsyncSession,
    candidate,
    target,
    result,
    semantic,
) -> dict:
    """单条 JD 详情（下拉切换用）：三维得分 + 差距 + 学习路径 + JD 原文。

    组装口径与 compare 对"最佳 JD"一致，供同岗位名下的单 JD 切换复用。
    不含岗位级字段（domain_name/weights/evidence_refs），由前端并入原详情。
    """
    path = await LearningPathGenerator().generate(candidate, target, semantic=semantic)
    jd_original = None
    try:
        jd_row = await db.get(JDRaw, int(target.position_id))
    except (TypeError, ValueError):
        jd_row = None
    if jd_row is not None:
        jd_original = {
            "jd_title": str((jd_row.snapshot or {}).get("title") or target.name).strip(),
            "source": jd_row.source or "",
            "source_url": jd_row.source_url or "",
            "text": (jd_row.raw_text or "").strip()[:8000],
            "score": round(result.total_score, 4),
        }
    body = result.model_dump()
    body.pop("position_id", None)  # 岗位 id 由外层岗位名统一维护，避免覆盖
    return {
        **body,
        "position_name": target.name,
        "gaps": [g.model_dump() for g in path.gaps],
        "learning_path": [item.model_dump() for item in path.items],
        "learning_path_blocked": path.blocked,
        "learning_path_block_reason": path.block_reason,
        "jd_original": jd_original,
    }


@router.get("/result/{match_id}/jd/{jd_id}")
async def match_result_jd_detail(
    match_id: str,
    jd_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("user")),
):
    """[M5] 获取比对岗位下某条 JD 的详情（下拉逐条查看）。

    用 match:result 快照内的 resume_id + position 重建候选人，对选定 jd_id 单条
    评分并生成差距/学习路径/原文，返回与该 JD 一一对应的详情（前端并入当前岗位
    详情展示；岗位级字段如 domain_name/weights 不变）。
    """
    data = await _load_or_404(match_id, user)
    resume_id = data.get("resume_id")
    if not resume_id:
        return error(ERR_NOT_FOUND, "该匹配快照无 JD 明细所需信息", http_status=404)
    cache = await db.get(ResumeCache, resume_id)
    if cache is None:
        return error(ERR_NOT_FOUND, "简历不存在或已删除", http_status=404)

    candidate = build_candidate(cache.parsed_data)
    semantic = SkillEmbedder.get()
    try:
        project_vectors = await load_project_vectors(db, resume_id)
    except PgvectorUnavailableError:
        project_vectors = {}
    found = await score_jd_one(db, candidate, jd_id, project_vectors, semantic=semantic)
    if found is None:
        return error(ERR_NOT_FOUND, "该 JD 不存在或无抽取快照", http_status=404)
    target, result = found
    payload = await _compose_jd_detail(db, candidate, target, result, semantic)
    payload["position_id"] = data.get("position_id") or ""
    return ok(data=payload)


@router.get("/result/{match_id}/gap")
async def match_result_gap(match_id: str, user: dict = Depends(require_role("user"))):
    """[M4] 获取差距分析（compare 结果的 gaps 三态列表，仅限本人结果）。"""
    data = await _load_or_404(match_id, user)
    return ok(data={"match_id": match_id, "gaps": data.get("gaps", [])})


@router.get("/result/{match_id}/path")
async def match_result_path(match_id: str, user: dict = Depends(require_role("user"))):
    """[M4] 获取学习路径（compare 结果的 missing/weak 技能先修链 + 课程，仅限本人结果）。"""
    data = await _load_or_404(match_id, user)
    return ok(
        data={
            "match_id": match_id,
            "learning_path": data.get("learning_path", []),
            "learning_path_blocked": bool(data.get("learning_path_blocked")),
            "learning_path_block_reason": data.get("learning_path_block_reason"),
        }
    )


@router.post("/result/{match_id}/diagnosis", status_code=202)
async def request_match_diagnosis(
    match_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("user")),
):
    """Create or reuse an asynchronous diagnosis task."""
    user_id = user.get("sub", "")
    data = await _load_match_result(match_id, user_id)
    if data is None:
        return error(ERR_NOT_FOUND, "匹配结果不存在或已过期", http_status=404)
    if not data.get("gaps"):
        return error(ERR_VALIDATION, "该匹配结果无差距数据，仅人岗比对可生成诊断报告")

    cached = await redis_client.get(f"match:diagnosis:{match_id}")
    if cached:
        return ok(data={
            "task_id": "",
            "status": "success",
            "match_id": match_id,
            "report": json.loads(cached),
            "error": "",
        })

    existing = await db.scalar(
        select(TaskStatus)
        .where(
            TaskStatus.task_type == "generate_diagnosis",
            TaskStatus.result["match_id"].astext == match_id,
            TaskStatus.result["user_id"].astext == user_id,
            TaskStatus.status.in_(["pending", "running"]),
        )
        .order_by(TaskStatus.created_at.desc())
    )
    if existing is not None:
        return ok(data={
            "task_id": existing.id,
            "status": existing.status,
            "match_id": match_id,
            "report": None,
            "error": existing.error or "",
        })

    task = TaskStatus(
        task_type="generate_diagnosis",
        status="pending",
        result={"match_id": match_id, "user_id": user_id},
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    try:
        await enqueue(
            "generate_diagnosis",
            match_id=match_id,
            task_id=str(task.id),
            user_id=user_id,
        )
    except Exception as exc:
        task.status = "failed"
        task.error = "任务入队失败"
        await db.commit()
        logger.error("诊断任务入队失败: task_id=%s err=%s", task.id, exc)
    return ok(data={
        "task_id": task.id,
        "status": task.status,
        "match_id": match_id,
        "report": None,
        "error": task.error or "",
    })


@router.get("/result/{match_id}/diagnosis")
async def match_diagnosis(
    match_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("user")),
):
    """[M4] 获取人岗比对诊断报告（只读：24h 缓存 → PG 耐久回退）。

    生成一律走 POST 异步任务（worker 唯一执行路径：30s 超时 + provider 降级
    + TaskStatus 状态机）。本端点不再同步调用 LLM——同步/异步双路径并存会
    绕过任务状态机重复生成、且超时语义分叉（08-23 闭环审查 P0）。Redis
    过期后从 DiagnosisReportRecord 落库报告回读并回填缓存（耐久镜像补上
    读取回退）；两者皆无 → 404，前端应先 POST 创建生成任务。
    """
    data = await _load_or_404(match_id, user)
    if not data.get("gaps"):
        return error(ERR_VALIDATION, "该匹配结果无差距数据，仅人岗比对可生成诊断报告")

    cached = await redis_client.get(f"match:diagnosis:{match_id}")
    if cached:
        return ok(data=json.loads(cached))

    row = await db.scalar(
        select(DiagnosisReportRecord).where(
            DiagnosisReportRecord.match_id == match_id
        )
    )
    if row is not None and isinstance(row.report, dict):
        try:
            await redis_client.set(
                f"match:diagnosis:{match_id}",
                json.dumps(row.report, ensure_ascii=False),
                ex=_MATCH_RESULT_TTL,
            )
        except Exception:
            logger.warning("诊断报告缓存回填失败（不影响本次响应）", exc_info=True)
        return ok(data=row.report)

    return error(
        ERR_NOT_FOUND,
        "诊断报告尚未生成或已过期，请先创建诊断任务（POST /match/result/{match_id}/diagnosis）",
        http_status=404,
    )


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
