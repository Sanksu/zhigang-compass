"""管理后台路由：用户管理 / 审计日志 / 爬虫状态 / 岗位审核（RBAC admin only）。

对齐契约 /api/v1/admin/*。岗位审核（positions/pending）读取 DiscoveryCandidate 表
（默认过滤 state=candidate），review 走状态机校验 + 图谱 status 同步 + 审计日志。
"""

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.common import iso, paged_ok, paginate, serialize_task, sse_task_events
from app.api.deps import require_permission
from app.api.v1.admin_routes import accounts, audit, crawl
from app.core.arq_client import enqueue
from app.core.database import get_db, redis_client
from app.core.errors import ERR_CONFLICT, ERR_INTERNAL, ERR_NOT_FOUND, ERR_VALIDATION
from app.models.business import RejectedChange, TaskStatus
from app.schemas.common import error, ok
from app.services.kg.id_generator import next_id

router = APIRouter(prefix="/admin", dependencies=[Depends(require_permission("admin:*"))])
router.include_router(accounts.router)
router.include_router(audit.router)
router.include_router(crawl.router)

# 爬虫域私有符号 re-export（tests/admin/test_crawl_trigger 直连导入）
PLATFORM_META = crawl.PLATFORM_META
_PLATFORM_TO_SPIDER = crawl._PLATFORM_TO_SPIDER
_history_row = crawl._history_row
_match_platform = crawl._match_platform

logger = logging.getLogger(__name__)


# ============================================================
# 爬取管理（BE-M4-05）：手动触发爬取任务
# ============================================================


async def _enqueue_crawl(
    spider: str,
    keywords: list[str],
    cities: list[str] | None = None,
    task_id: str | None = None,
) -> None:
    """入队 ARQ crawl_platform 任务；队列不可用抛异常由调用方标记 failed。"""
    logger.info(
        f"[_enqueue_crawl] 准备入队: task_id={task_id} spider={spider} "
        f"keywords={keywords} cities={cities or '(默认)'}"
    )
    # task_id 供 crawl_platform 实时写日志队列 + 更新任务状态（SSE 端点消费）
    kwargs = {"spider_name": spider, "keywords": keywords, "task_id": task_id}
    if cities:
        kwargs["cities"] = cities
    await enqueue("crawl_platform", **kwargs)
    logger.info(f"[_enqueue_crawl] 入队成功: task_id={task_id} job=crawl_platform kwargs={kwargs}")


@router.post("/crawl/trigger", status_code=202)
async def crawl_trigger(req: dict, db: AsyncSession = Depends(get_db)):
    """触发爬取任务（BE-M4-05，契约 /admin/crawl/trigger）。

    校验平台（PLATFORM_META 白名单）→ 建 TaskStatus(pending) → 入队 ARQ
    crawl_platform → 返回 task_id。队列不可用时标记任务 failed 并返回 500。
    keyword 留空 = 采集平台热度/最新内容（08-16 用户决策）。
    """
    platform = (req.get("platform") or "").strip()
    keyword = (req.get("keyword") or "").strip()
    city = (req.get("city") or "").strip()
    logger.info(f"[crawl/trigger] 收到触发请求: platform={platform} keyword={keyword or '(空=热度/最新)'} city={city or '(默认)'}")
    if platform not in _PLATFORM_TO_SPIDER:
        logger.warning(f"[crawl/trigger] 未知平台: {platform}")
        return error(ERR_VALIDATION, f"未知平台: {platform}（可选: {', '.join(sorted(PLATFORM_META))}）")

    try:
        task = TaskStatus(
            task_type="crawl",
            status="pending",
            result={"platform": platform, "keyword": keyword, "city": city or None},
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
    except Exception as e:
        logger.exception(
            f"[crawl/trigger] 任务落库失败: platform={platform} keyword={keyword} city={city or '(默认)'} err={e}"
        )
        # 08-14 审查：异常详情仅入服务端日志，不随响应外泄（错误详情泄露漏网点）
        return error(ERR_INTERNAL, "爬取任务落库失败，请稍后重试")
    logger.info(f"[crawl/trigger] 任务已建: task_id={task.id} platform={platform} keyword={keyword} city={city or '(默认)'}")

    try:
        await _enqueue_crawl(
            _PLATFORM_TO_SPIDER[platform],
            [keyword] if keyword else [],  # 空关键词 = 平台热度/最新采集（08-16 用户决策）
            cities=[city] if city else None,
            task_id=str(task.id),
        )
        logger.info(f"[crawl/trigger] 任务入队成功: task_id={task.id} spider={_PLATFORM_TO_SPIDER[platform]}")
    except Exception as e:
        task.status = "failed"
        task.error = "任务入队失败"  # 固定文案：详情仅入日志，防经 /crawl/history 透传内部信息
        await db.commit()
        logger.error(f"[crawl/trigger] 任务入队失败: task_id={task.id} err={e}")
        return error(ERR_INTERNAL, "爬取任务入队失败，请稍后重试")

    return ok(data={"task_id": task.id, "platform": platform, "status": "pending"})


# ============================================================
# 爬虫实时日志（SSE）：手动触发后逐行推送 scrapy 终端输出
# ============================================================

async def _crawl_log_events(
    task_uuid: str,
    get_logs,
    get_task,
    *,
    poll_interval: float = 0.5,
    timeout: float = 600.0,
):
    """爬虫实时日志 SSE 事件序列（可注入日志/任务查询函数便于测试）。

    事件流：log（每行 scrapy 输出）→ progress（周期心跳，含任务状态）→
    终态 success 推送 done、failed 推送 error 后关闭；任务不存在/超时推送
    error 后关闭。日志按 offset 增量拉取，避免重复推送。
    """
    offset = 0

    async def _poll_logs() -> list[str]:
        nonlocal offset
        try:
            lines = await get_logs(task_uuid, offset)
        except Exception:
            lines = []
        offset += len(lines)
        return [
            f"event: log\ndata: {json.dumps({'line': ln}, ensure_ascii=False)}\n\n"
            for ln in lines
        ]

    def _progress(task) -> dict:
        return {"status": task["status"], "progress": task["progress"]}

    async for event in sse_task_events(
        task_uuid,
        get_task,
        before_poll=_poll_logs,
        progress_payload=_progress,
        poll_interval=poll_interval,
        timeout=timeout,
    ):
        yield event


@router.get("/crawl/task/{task_id}/stream")
async def crawl_task_stream(task_id: str):
    """SSE 实时推送爬虫终端日志（手动触发场景，BE-M4-05 扩展）。

    日志来源为 crawl_platform 逐行写入 Redis 的 LIST（crawl:log:{task_id}，
    TTL 1h），按 offset 增量拉取；任务状态由 TaskStatus 驱动终态
    （success → done / failed → error）。任务不存在 / 推送超时（600s）结束。
    """
    try:
        task_uuid = str(uuid.UUID(task_id))
    except (ValueError, AttributeError):
        return error(ERR_VALIDATION, "task_id 格式非法")

    from app.core.arq_client import get_pool

    async def _get_task(tid: str) -> dict | None:
        from app.core.database import async_session_factory
        from app.models.business import TaskStatus

        async with async_session_factory() as session:
            task = await session.get(TaskStatus, tid)
        return serialize_task(task) if task is not None else None

    async def _get_logs(tid: str, start: int) -> list[str]:
        # 复用模块级 ARQ 连接池（08-14 审查：此前每 0.5s 新建池，600s 轮询 ≈ 1200 次建连）
        pool = await get_pool()
        raw = await pool.lrange(f"crawl:log:{tid}", start, -1)
        return [ln.decode("utf-8", errors="replace") if isinstance(ln, bytes) else str(ln) for ln in raw]

    async def _event_gen():
        async for event in _crawl_log_events(task_uuid, _get_logs, _get_task):
            yield event

    return StreamingResponse(_event_gen(), media_type="text/event-stream")


@router.get("/crawl/history")
async def crawl_history(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """爬取历史（BE-M4-05 扩展）：task_status 中 crawl 任务列表，倒序分页。

    字段来源 task.result（触发时写 platform/keyword，crawl_platform 合并写入
    spider/output_file/items），status 为 pending/running/success/failed。
    """
    stmt = select(TaskStatus).where(TaskStatus.task_type == "crawl")
    count_stmt = (
        select(func.count()).select_from(TaskStatus).where(TaskStatus.task_type == "crawl")
    )
    rows, total = await paginate(
        db, stmt.order_by(TaskStatus.created_at.desc()), page, size, count_stmt=count_stmt
    )
    return paged_ok([_history_row(t) for t in rows], total, page, size)


# ============================================================
# 岗位审核（AL-M4-01 新岗位发现：候选池 pending 列表 + 审核流转）
# ============================================================

async def _persist_rejected_change(
    db: AsyncSession, position_name: str, change_type: str, reason: str
) -> None:
    """审核驳回变更落库（§11.4.1 rejected_changes：驳回可追溯）。"""
    db.add(
        RejectedChange(
            position_name=position_name, change_type=change_type, reason=reason
        )
    )
    await db.commit()


@router.get("/positions/pending")
async def positions_pending(
    state: str = Query(default="candidate", pattern="^(candidate|emerging|stable|declining)$"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """待审核岗位列表（新岗位发现候选池）。

    默认返回 candidate（待 admin 审核是否晋升 emerging），可切换状态过滤。
    （08-15 修复：此前 state 缺省不过滤——摘要/徽标把已晋升 emerging/stable
    计入"待审核"，29 条中真待办仅 2 条 candidate。）
    """
    from app.models.business import DiscoveryCandidate

    stmt = select(DiscoveryCandidate)
    count_stmt = select(func.count()).select_from(DiscoveryCandidate)
    if state:
        stmt = stmt.where(DiscoveryCandidate.state == state)
        count_stmt = count_stmt.where(DiscoveryCandidate.state == state)
    rows, total = await paginate(
        db, stmt.order_by(DiscoveryCandidate.detected_at.desc()), page, size,
        count_stmt=count_stmt,
    )
    items = [
        {
            "id": c.id,
            "position_name": c.position_name,
            "state": c.state,
            "features": c.features,
            "confidence": c.confidence,
            "evidence_refs": c.evidence_refs,
            "seed_matched": c.seed_matched,
            "rag_matched": c.rag_matched,
            "definition_draft": c.definition_draft,
            "detected_at": c.detected_at,
            "updated_at": iso(c.updated_at),
        }
        for c in rows
    ]
    return paged_ok(items, total, page, size)


@router.post("/positions/{candidate_id}/review")
async def review_position(
    candidate_id: str,
    req: dict,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("admin:*")),
):
    """审核 candidate：approve → emerging / reject → rejected。

    流程：读候选池 → 组装 CandidatePosition → 状态机校验（emerging 需
    置信度 ≥ 0.6 AND 源 ≥ 2）→ Neo4j Position.status 同步 → 写审计日志
    → 更新候选池状态。

    Args:
        req: {"action": "approve" | "reject", "reason": "..."}，reason 必填
    """
    from app.models.business import DiscoveryCandidate
    from app.services.discovery.schemas import CandidatePosition, DiscoveryFeatures, PositionState

    action = req.get("action")
    reason = (req.get("reason") or "").strip()
    if action not in ("approve", "reject"):
        return error(ERR_VALIDATION, "action 必须为 approve 或 reject")
    if not reason:
        return error(ERR_VALIDATION, "审核必须填写 reason")

    cand_row = await db.get(DiscoveryCandidate, candidate_id)
    if cand_row is None:
        return error(ERR_NOT_FOUND, "候选岗位不存在", http_status=404)
    if cand_row.state != "candidate":
        return error(ERR_CONFLICT, f"候选岗位当前状态 {cand_row.state}，不可审核")

    features = DiscoveryFeatures(**cand_row.features)
    candidate = CandidatePosition(
        candidate_id=cand_row.id,
        position_name=cand_row.position_name,
        state=PositionState.CANDIDATE,
        features=features,
        detected_at=cand_row.detected_at,
        evidence_refs=cand_row.evidence_refs,
        seed_matched=cand_row.seed_matched,
        rag_matched=cand_row.rag_matched,
        definition_draft=cand_row.definition_draft,
    )
    target = PositionState.EMERGING if action == "approve" else PositionState.REJECTED
    if action == "approve":
        # 置信度 ≥ 0.6 AND 源多样性 ≥ 2 才允许晋升（设计文档 7.2.4 阈值表）
        from app.services.discovery.state_machine import can_promote_to_emerging

        conf = cand_row.confidence or {}
        if not can_promote_to_emerging(candidate, confidence=float(conf.get("final_confidence", 0.0))):
            return error(ERR_VALIDATION, "置信度 < 0.6 或独立源 < 2，不满足 emerging 晋升条件")

    updated = await asyncio.to_thread(
        _persist_position_state,
        candidate,
        target,
        db,
        current_user.get("sub") or current_user.get("user_id", "admin"),
        reason,
    )

    cand_row.state = updated.state.value
    if action == "reject":
        # 审核驳回变更落库（§11.4.1 rejected_changes），驳回可追溯
        await _persist_rejected_change(db, cand_row.position_name, "discovery_reject", reason)
    await db.commit()

    return ok(
        data={
            "id": cand_row.id,
            "position_name": cand_row.position_name,
            "state": cand_row.state,
            "reason": reason,
        },
        msg=f"已{'通过晋升 emerging' if action == 'approve' else '驳回'}: {cand_row.position_name}",
    )


# ============================================================
# 岗位演化审核（[M4]：emerging → stable / declining 人工确认）
# ============================================================

@router.get("/evolution/pending")
async def evolution_pending(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """[M4] 待审核演化变更：emerging 状态岗位列表。

    与 /positions/pending（candidate 待晋升）互补——这里聚焦已晋升
    emerging 的岗位，需 admin 确认晋级 stable 或判定进入 declining。
    """
    from app.models.business import DiscoveryCandidate

    stmt = select(DiscoveryCandidate).where(DiscoveryCandidate.state == "emerging")
    count_stmt = select(func.count()).select_from(DiscoveryCandidate).where(
        DiscoveryCandidate.state == "emerging"
    )
    rows, total = await paginate(
        db, stmt.order_by(DiscoveryCandidate.updated_at.desc()), page, size,
        count_stmt=count_stmt,
    )
    items = [
        {
            "id": c.id,
            "position_name": c.position_name,
            "state": c.state,
            "confidence": c.confidence,
            "evidence_refs": c.evidence_refs,
            "seed_matched": c.seed_matched,
            "rag_matched": c.rag_matched,
            "definition_draft": c.definition_draft,
            "detected_at": c.detected_at,
            "updated_at": iso(c.updated_at),
        }
        for c in rows
    ]
    return paged_ok(items, total, page, size)


@router.put("/evolution/{candidate_id}/review")
async def review_evolution(
    candidate_id: str,
    req: dict,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("admin:*")),
):
    """[M4] 审核演化变更：emerging 岗位 approve → stable / reject → declining。

    复用六状态机（PositionStateMachine）持久化 Neo4j Position.status，
    approve 且携带 modified 时合并进候选池 features（演化确认的属性修订）。

    Args:
        req: {"action": "approve" | "reject", "modified": {...}?}
    """
    from app.models.business import DiscoveryCandidate
    from app.services.discovery.schemas import CandidatePosition, DiscoveryFeatures, PositionState

    action = req.get("action")
    if action not in ("approve", "reject"):
        return error(ERR_VALIDATION, "action 必须为 approve 或 reject")

    cand_row = await db.get(DiscoveryCandidate, candidate_id)
    if cand_row is None:
        return error(ERR_NOT_FOUND, "候选岗位不存在", http_status=404)
    if cand_row.state != "emerging":
        return error(ERR_CONFLICT, f"候选岗位当前状态 {cand_row.state}，仅 emerging 可执行演化审核")

    candidate = CandidatePosition(
        candidate_id=cand_row.id,
        position_name=cand_row.position_name,
        state=PositionState.EMERGING,
        features=DiscoveryFeatures(**cand_row.features),
        detected_at=cand_row.detected_at,
        evidence_refs=cand_row.evidence_refs,
        seed_matched=cand_row.seed_matched,
        rag_matched=cand_row.rag_matched,
        definition_draft=cand_row.definition_draft,
    )
    target = PositionState.STABLE if action == "approve" else PositionState.DECLINING

    updated = await asyncio.to_thread(
        _persist_position_state,
        candidate,
        target,
        db,
        current_user.get("sub") or current_user.get("user_id", "admin"),
        (req.get("reason") or "").strip() or "admin evolution review",
    )

    cand_row.state = updated.state.value
    modified = req.get("modified")
    if action == "approve" and isinstance(modified, dict) and modified:
        cand_row.features = {**(cand_row.features or {}), **modified}
    await db.commit()

    return ok(
        data={
            "id": cand_row.id,
            "position_name": cand_row.position_name,
            "state": cand_row.state,
        },
        msg=f"已{'确认晋级 stable' if action == 'approve' else '确认衰退 declining'}: {cand_row.position_name}",
    )


@router.get("/positions/declining")
async def positions_declining(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """[M4] 待归档岗位列表：declining 状态（admin 确认衰退 → archived 终态）。

    与 /positions/pending（candidate）、/evolution/pending（emerging）并列，
    覆盖六状态机全部人工审核入口。
    """
    from app.models.business import DiscoveryCandidate

    stmt = select(DiscoveryCandidate).where(DiscoveryCandidate.state == "declining")
    count_stmt = select(func.count()).select_from(DiscoveryCandidate).where(
        DiscoveryCandidate.state == "declining"
    )
    rows, total = await paginate(
        db, stmt.order_by(DiscoveryCandidate.updated_at.desc()), page, size,
        count_stmt=count_stmt,
    )
    items = [
        {
            "id": c.id,
            "position_name": c.position_name,
            "state": c.state,
            "confidence": c.confidence,
            "evidence_refs": c.evidence_refs,
            "detected_at": c.detected_at,
            "updated_at": iso(c.updated_at),
        }
        for c in rows
    ]
    return paged_ok(items, total, page, size)


@router.put("/positions/{candidate_id}/archive")
async def archive_position(
    candidate_id: str,
    req: dict,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("admin:*")),
):
    """[M4] 确认衰退归档：declining → archived（终态）。

    六状态机最后一环：人工确认后 Neo4j Position.status 同步 + AuditLog
    记录（reason 必填）。与 /positions/{id}/review（candidate → emerging/
    rejected）和 /evolution/{id}/review（emerging → stable/declining）并列。
    """
    from app.models.business import DiscoveryCandidate
    from app.services.discovery.schemas import CandidatePosition, DiscoveryFeatures, PositionState

    reason = (req.get("reason") or "").strip()
    if not reason:
        return error(ERR_VALIDATION, "归档必须填写 reason")

    cand_row = await db.get(DiscoveryCandidate, candidate_id)
    if cand_row is None:
        return error(ERR_NOT_FOUND, "候选岗位不存在", http_status=404)
    if cand_row.state != "declining":
        return error(ERR_CONFLICT, f"候选岗位当前状态 {cand_row.state}，仅 declining 可归档")

    candidate = CandidatePosition(
        candidate_id=cand_row.id,
        position_name=cand_row.position_name,
        state=PositionState.DECLINING,
        features=DiscoveryFeatures(**cand_row.features),
        detected_at=cand_row.detected_at,
        evidence_refs=cand_row.evidence_refs,
        seed_matched=cand_row.seed_matched,
        rag_matched=cand_row.rag_matched,
        definition_draft=cand_row.definition_draft,
    )
    updated = await asyncio.to_thread(
        _persist_position_state,
        candidate,
        PositionState.ARCHIVED,
        db,
        current_user.get("sub") or current_user.get("user_id", "admin"),
        reason,
    )
    cand_row.state = updated.state.value
    await db.commit()

    return ok(
        data={
            "id": cand_row.id,
            "position_name": cand_row.position_name,
            "state": cand_row.state,
        },
        msg=f"已归档（终态）: {cand_row.position_name}",
    )


# ============================================================
# 岗位人工编辑（设计文档 12.2：审核员直接编辑岗位定义，改动写 PositionEditLog）
# ============================================================

# 技能权重默认值：图谱 REQUIRES 关系未持久化 weight 时按 1.0 展示（与 match.py 同口径）
DEFAULT_SKILL_WEIGHT = 1.0
NECESSITY_WHITELIST = ("must", "nice")


def validate_position_edit(skills, core_duties, scenarios) -> str | None:
    """校验岗位编辑请求，返回错误信息或 None。

    约束：skills 每项 name 非空、necessity ∈ {must, nice}、weight ∈ [0.0, 1.0]；
    core_duties/scenarios 提供时必须是字符串数组。
    """
    if skills is not None:
        if not isinstance(skills, list):
            return "skills 必须是数组"
        for i, s in enumerate(skills):
            if not isinstance(s, dict):
                return f"skills[{i}] 必须是对象"
            name = (s.get("name") or "").strip()
            if not name:
                return f"skills[{i}] 缺少 name"
            if s.get("necessity") not in NECESSITY_WHITELIST:
                return f"技能 '{name}' 的 necessity 必须为 must 或 nice"
            weight = s.get("weight")
            if (
                isinstance(weight, bool)
                or not isinstance(weight, (int, float))
                or not 0.0 <= weight <= 1.0
            ):
                return f"技能 '{name}' 的 weight 必须在 0.0-1.0 之间"
    for field, value in (("core_duties", core_duties), ("scenarios", scenarios)):
        if value is not None and (
            not isinstance(value, list) or not all(isinstance(x, str) for x in value)
        ):
            return f"{field} 必须是字符串数组"
    return None


def position_edit_diff(current: dict, skills, core_duties, scenarios) -> str:
    """生成编辑 diff 摘要（如 'skills +A/B, ~C, -D; core_duties 更新'）。

    技能按 name 对比：+ 新增、~ 变更（necessity/weight）、- 移除；
    文本字段实际变化时以 '字段名 更新' 追加。无变更返回空串。
    """
    parts = []
    if skills is not None:
        current_skills = {s["name"]: s for s in current["skills"]}
        new_skills = {s["name"]: s for s in skills}
        added = sorted(set(new_skills) - set(current_skills))
        removed = sorted(set(current_skills) - set(new_skills))
        updated = sorted(
            n
            for n in set(current_skills) & set(new_skills)
            if (current_skills[n]["necessity"], current_skills[n]["weight"])
            != (new_skills[n]["necessity"], new_skills[n]["weight"])
        )
        if added or removed or updated:
            ops = []
            if added:
                ops.append("+" + "/".join(added))
            if updated:
                ops.append("~" + "/".join(updated))
            if removed:
                ops.append("-" + "/".join(removed))
            parts.append("skills " + ", ".join(ops))
    if core_duties is not None and core_duties != current.get("core_duties", []):
        parts.append("core_duties 更新")
    if scenarios is not None and scenarios != current.get("scenarios", []):
        parts.append("scenarios 更新")
    return "; ".join(parts)


def _persist_position_state(candidate, target, db, operator: str, reason: str):
    """岗位状态持久化（Neo4j 同步驱动 + 审计日志），线程池执行。

    Neo4j 驱动为同步实现，放线程池避免阻塞事件循环；db.add 仅操作
    Session 内存态（不触 IO），commit 由调用方在主线程完成。
    """
    from app.core.database import neo4j_driver
    from app.services.discovery.state_machine import PositionStateMachine

    machine = PositionStateMachine()
    with neo4j_driver.session() as neo4j_session:
        return machine.persist(
            neo4j_session, candidate, target, db=db, operator=operator, reason=reason
        )


def _query_position_detail(position_name: str) -> dict | None:
    """岗位详情读取（Neo4j 同步驱动，线程池执行）。"""
    from app.core.database import neo4j_driver

    with neo4j_driver.session() as session:
        return session.execute_read(_get_position_detail_tx, position_name)


def _edit_position_neo4j(position_name: str, editor_id, skills, core_duties, scenarios) -> dict:
    """岗位编辑写（Neo4j 同步驱动，线程池执行）。"""
    from app.core.database import neo4j_driver

    with neo4j_driver.session() as session:
        return session.execute_write(
            _edit_position_tx, position_name, editor_id, skills, core_duties, scenarios
        )


def _get_position_detail_tx(tx, position_name: str) -> dict | None:
    """读岗位详情（Position 属性 + REQUIRES 技能/学历/证书），岗位不存在返回 None。"""
    pos = tx.run(
        """
        MATCH (p:Position {name: $name})
        RETURN p.id AS id, p.name AS name, p.level AS level, p.industry AS industry,
               p.salary_range AS salary_range, p.status AS status,
               p.core_duties AS core_duties, p.scenarios AS scenarios,
               p.created_at AS created_at, p.updated_at AS updated_at
        """,
        name=position_name,
    ).single()
    if pos is None:
        return None

    detail = {
        "id": pos["id"],
        "name": pos["name"],
        "level": pos["level"] or "",
        "industry": pos["industry"] or "",
        "salary_range": pos["salary_range"] or "",
        "status": pos["status"] or "",
        "core_duties": pos["core_duties"] or [],
        "scenarios": pos["scenarios"] or [],
        "created_at": pos["created_at"] or "",
        "updated_at": pos["updated_at"] or "",
        "skills": [],
        "education": [],
        "certifications": [],
    }
    for rec in tx.run(
        """
        MATCH (p:Position {name: $name})-[r:REQUIRES]->(target)
        WHERE target:Skill OR target:Education OR target:Certification
        RETURN CASE
                   WHEN target:Skill THEN 'skill'
                   WHEN target:Education THEN 'education'
                   WHEN target:Certification THEN 'certification'
               END AS kind,
               target.name AS name, r.necessity AS necessity,
               r.weight AS weight, r.level AS level
        """,
        name=position_name,
    ):
        entry = {
            "name": rec["name"],
            "necessity": rec["necessity"],
            "level": rec["level"] or "",
        }
        if rec["kind"] == "skill":
            weight = rec["weight"]
            entry["weight"] = float(weight if weight is not None else DEFAULT_SKILL_WEIGHT)
            detail["skills"].append(entry)
        elif rec["kind"] == "education":
            detail["education"].append(entry)
        else:
            detail["certifications"].append(entry)
    return detail


def _edit_position_tx(tx, position_name, editor_id, skills, core_duties, scenarios) -> dict:
    """执行岗位编辑（技能全量替换 + 文本字段更新），有实际变更时写 PositionEditLog。

    Returns:
        {"exists": bool, "updated": bool, "diff_summary": str}；
        exists=False 表示岗位不存在（不做任何写入）。
    """
    current = _get_position_detail_tx(tx, position_name)
    if current is None:
        return {"exists": False, "updated": False, "diff_summary": ""}

    diff_summary = position_edit_diff(current, skills, core_duties, scenarios)
    if not diff_summary:
        return {"exists": True, "updated": False, "diff_summary": "", "id": current["id"]}

    now = datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")
    if skills is not None:
        # 全量替换：逐个 MERGE Skill 节点 + REQUIRES 关系并 SET necessity/weight，
        # 新增与更新幂等合一；仅删关系的技能不删节点（Skill 可被其他岗位复用）
        for s in skills:
            tx.run(
                """
                MATCH (p:Position {name: $position_name})
                MERGE (sk:Skill {name: $skill_name})
                MERGE (p)-[r:REQUIRES]->(sk)
                SET r.necessity = $necessity, r.weight = $weight
                """,
                position_name=position_name,
                skill_name=s["name"],
                necessity=s["necessity"],
                weight=s["weight"],
            )
        current_names = {s["name"] for s in current["skills"]}
        new_names = {s["name"] for s in skills}
        for name in sorted(current_names - new_names):
            tx.run(
                """
                MATCH (p:Position {name: $position_name})-[r:REQUIRES]->(sk:Skill {name: $skill_name})
                DELETE r
                """,
                position_name=position_name,
                skill_name=name,
            )

    # 文本字段按提供项动态 SET（字段名来自固定白名单，无注入面）
    set_clauses = ["p.updated_at = $now"]
    params = {"name": position_name, "now": now}
    if core_duties is not None:
        set_clauses.append("p.core_duties = $core_duties")
        params["core_duties"] = core_duties
    if scenarios is not None:
        set_clauses.append("p.scenarios = $scenarios")
        params["scenarios"] = scenarios
    tx.run(f"MATCH (p:Position {{name: $name}}) SET {', '.join(set_clauses)}", **params)

    # 编辑日志（§12.2：审核员 ID + 时间戳 + diff 摘要，支持版本回溯）
    tx.run(
        """
        CREATE (l:PositionEditLog {
            id: $id,
            position_name: $position_name,
            editor_id: $editor_id,
            created_at: $created_at,
            diff_summary: $diff_summary
        })
        """,
        id=next_id(tx, "PositionEditLog"),
        position_name=position_name,
        editor_id=editor_id,
        created_at=now,
        diff_summary=diff_summary,
    )
    return {"exists": True, "updated": True, "diff_summary": diff_summary}


@router.get("/positions/{position_name}")
async def get_position_detail(position_name: str):
    """岗位详情（§12.2 岗位人工编辑：编辑前查看技能/学历/证书与文本定义）。"""
    detail = await asyncio.to_thread(_query_position_detail, position_name)
    if detail is None:
        return error(ERR_NOT_FOUND, f"岗位不存在: {position_name}", http_status=404)
    return ok(data=detail)


@router.put("/positions/{position_name}")
async def update_position_definition(
    position_name: str,
    req: dict,
    current_user: dict = Depends(require_permission("admin:*")),
):
    """人工编辑岗位定义（§12.2），所有实际变更写入 PositionEditLog 节点。

    请求体（均可选，无字段时为空操作返回"无变更"）：
        skills: 技能列表全量替换，每项 {name, necessity: must|nice, weight: 0.0-1.0}
        core_duties / scenarios: 字符串数组，更新 Position 节点属性
    """
    skills = req.get("skills")
    core_duties = req.get("core_duties")
    scenarios = req.get("scenarios")
    err = validate_position_edit(skills, core_duties, scenarios)
    if err:
        return error(ERR_VALIDATION, err)

    editor_id = current_user.get("sub") or current_user.get("user_id", "admin")
    result = await asyncio.to_thread(
        _edit_position_neo4j, position_name, editor_id, skills, core_duties, scenarios
    )
    if not result["exists"]:
        return error(ERR_NOT_FOUND, f"岗位不存在: {position_name}", http_status=404)
    # 编辑已生效：失效岗位详情缓存（graph.py key 为 graph:position:{id}:{scope}，
    # all=全量可见，public=公开态），避免用户读到 5min 旧数据
    if result["id"]:
        await redis_client.delete(f"graph:position:{result['id']}:all")
        await redis_client.delete(f"graph:position:{result['id']}:public")
    return ok(
        data={
            "position_name": position_name,
            "updated": result["updated"],
            "diff_summary": result["diff_summary"],
        },
        msg="无变更" if not result["updated"] else "已保存编辑",
    )


# ============================================================
# LLM provider 配置（持久化到 llm_providers.yaml）
# ============================================================

_LLM_CONFIG_PATH = Path(__file__).resolve().parents[3] / "configs" / "llm_providers.yaml"


def mask_secret(value: str) -> str:
    """密钥打码：保留后 4 位，其余掩码；空值返回空串。"""
    value = (value or "").strip()
    if not value:
        return ""
    if len(value) <= 4:
        return "*" * len(value)
    return "*" * (len(value) - 4) + value[-4:]


def mask_providers(providers: list[dict]) -> list[dict]:
    """对 provider 列表的 api_key 打码（不修改入参）。"""
    return [{**p, "api_key": mask_secret(str(p.get("api_key") or ""))} for p in providers]


def validate_providers(providers: list) -> str | None:
    """校验 provider 列表，返回错误信息或 None。

    约束：非空列表；name 唯一且为安全字符；base_url 为 http(s) 地址；
    model 非空；priority 正整数且唯一；enabled 布尔。
    """
    if not isinstance(providers, list) or not providers:
        return "providers 必须是非空列表"
    seen_names: set[str] = set()
    seen_priorities: set[int] = set()
    for i, p in enumerate(providers):
        if not isinstance(p, dict):
            return f"第 {i + 1} 个 provider 必须是对象"
        name = (p.get("name") or "").strip()
        base_url = (p.get("base_url") or "").strip()
        model = (p.get("model") or "").strip()
        if not name:
            return f"第 {i + 1} 个 provider 缺少 name"
        if not re.match(r"^[A-Za-z0-9_-]+$", name):
            return f"name '{name}' 只能包含字母/数字/下划线/短横线"
        if name in seen_names:
            return f"name '{name}' 重复"
        seen_names.add(name)
        if not base_url.startswith(("http://", "https://")):
            return f"provider '{name}' 的 base_url 必须以 http(s):// 开头"
        if not model:
            return f"provider '{name}' 缺少 model"
        priority = p.get("priority")
        if not isinstance(priority, int) or priority < 1:
            return f"provider '{name}' 的 priority 必须为正整数"
        if priority in seen_priorities:
            return f"priority {priority} 重复（provider '{name}'）"
        seen_priorities.add(priority)
        if not isinstance(p.get("enabled", True), bool):
            return f"provider '{name}' 的 enabled 必须是布尔值"
    return None


def load_llm_config(path: Path) -> dict:
    """读取 yaml 配置。"""
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def save_llm_config(path: Path, providers: list) -> dict:
    """校验并写回 yaml，返回写回后的完整配置。

    api_key 为空白或含掩码（*）时保持原值，明文才更新；
    写回保留原文件头部注释（到顶层键 providers 之前）。
    """
    err = validate_providers(providers)
    if err:
        raise ValueError(err)

    text = Path(path).read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    old = {
        p["name"]: p for p in data.get("providers", [])
        if isinstance(p, dict) and p.get("name")
    }

    clean = []
    for p in providers:
        name = (p.get("name") or "").strip()
        api_key = (p.get("api_key") or "").strip()
        if not api_key or "*" in api_key:
            api_key = (old.get(name) or {}).get("api_key", "")
        entry = {
            "name": name,
            "priority": int(p["priority"]),
            "base_url": (p.get("base_url") or "").strip(),
            "api_key": api_key,
            "model": (p.get("model") or "").strip(),
            "supports_function_calling": bool(p.get("supports_function_calling", True)),
            "enabled": bool(p.get("enabled", True)),
        }
        # provider 特定请求参数（如 deepseek 关闭思考模式 thinking.type=disabled），非 dict 忽略
        extra_body = p.get("extra_body")
        if isinstance(extra_body, dict) and extra_body:
            entry["extra_body"] = extra_body
        clean.append(entry)
    data["providers"] = clean

    # 保留原文件头部注释块（到顶层键 providers: 为止），rest 由 dump 生成
    parts = re.split(r"^providers:\s*$", text, maxsplit=1, flags=re.M)
    header = parts[0] if len(parts) == 2 else ""
    body = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)
    Path(path).write_text(header + body, encoding="utf-8")

    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


@router.get("/llm-config")
async def get_llm_config():
    """读取当前生效 LLM provider 配置（api_key 打码，不明文回显）。"""
    try:
        cfg = load_llm_config(_LLM_CONFIG_PATH)
    except (OSError, yaml.YAMLError):
        return error(ERR_INTERNAL, "LLM 配置读取失败")
    cfg["providers"] = mask_providers(cfg.get("providers", []))
    return ok(data=cfg)


@router.put("/llm-config")
async def update_llm_config(req: dict):
    """保存 LLM provider 配置（持久化到 yaml，api_key 留空/掩码保持原值）。"""
    providers = req.get("providers")
    try:
        saved = save_llm_config(_LLM_CONFIG_PATH, providers)
    except ValueError as e:
        return error(ERR_VALIDATION, str(e))
    except (OSError, yaml.YAMLError):
        return error(ERR_INTERNAL, "LLM 配置保存失败")
    saved["providers"] = mask_providers(saved.get("providers", []))
    return ok(data=saved)


# ============================================================
# 运行时配置（08-16：管理后台 /admin/settings 可编辑、重启生效）
# ============================================================

@router.get("/runtime-config")
async def get_runtime_config():
    """读取运行时配置（非敏感运行参数；rate_limit 返回各源生效值）。"""
    from app.core import runtime_config

    data = runtime_config.load_all()
    # rate_limit 展示"默认 + 覆盖"合并后的生效值（crawlers.settings 启动时已合并）
    try:
        from crawlers.settings import RATE_LIMIT as CRAWLER_RATE_LIMIT

        data["rate_limit"] = {
            src: {
                "req_per_min": cfg.get("req_per_min", 4),
                "delay_range": [int(cfg["delay_range"][0]), int(cfg["delay_range"][1])]
                if cfg.get("delay_range") else None,
            }
            for src, cfg in CRAWLER_RATE_LIMIT.items()
        }
    except Exception:
        pass  # 独立运行环境无 crawlers 包时仅返回文件内容
    return ok(data=data)


@router.put("/runtime-config")
async def update_runtime_config(req: dict):
    """校验并持久化运行时配置（runtime_settings.json，重启后生效）。"""
    from app.core import runtime_config

    try:
        data = runtime_config.save(req)
    except ValueError as e:
        return error(ERR_VALIDATION, str(e))
    except OSError:
        return error(ERR_INTERNAL, "配置保存失败，请检查目录权限")
    return ok(data=data)


# ============================================================
# 技术热点观察池（设计文档 7.2.5，admin 周报可见）
# ============================================================

@router.get("/discovery/watch")
async def list_technology_watch(
    db: AsyncSession = Depends(get_db),
    status: str | None = Query(default=None, description="watch / candidate_promoted / archived"),
    source: str | None = Query(default=None, description="jd / arxiv / course / github / stackoverflow"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
):
    """观察池周报：技术热点信号列表（admin 可见，供运营周报/审核）。"""
    from app.models.business import TechnologyWatch

    stmt = select(TechnologyWatch).order_by(TechnologyWatch.updated_at.desc())
    if status:
        stmt = stmt.where(TechnologyWatch.status == status)
    if source:
        stmt = stmt.where(TechnologyWatch.signal_source == source)
    rows, total = await paginate(db, stmt, page, size)
    items = [
        {
            "skill_name": r.skill_name,
            "signal_source": r.signal_source,
            "signal_value": r.signal_value,
            "period": r.period,
            "status": r.status,
            "first_seen_at": iso(r.first_seen_at),
            "last_signal_at": iso(r.last_signal_at),
        }
        for r in rows
    ]
    return paged_ok(items, total, page, size)
