"""管理后台岗位审核域路由：候选池审核 / 演化审核 / 归档 / 技术观察池（RBAC admin only）。

对齐契约 /api/v1/admin/{positions,evolution,discovery}/*。review 走六状态机
（PositionStateMachine）校验；持久化顺序遵循 postmortems/003（08-24 事故整改，
同 dict_guard）：**PG 决策先行（状态 + 审计 + 驳回记录一次提交），图写副作用
最后**——图写（Neo4j Position.status + 缓存失效）不可回滚，放提交成功之后；
图写失败不回滚人工决策，以 effects_applied=False 透出待巡检对账。
"""

import asyncio
import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.common import iso, paged_ok, paginate
from app.api.deps import require_permission
from app.core.database import get_db
from app.core.errors import ERR_CONFLICT, ERR_NOT_FOUND, ERR_VALIDATION
from app.models.business import RejectedChange
from app.schemas.common import error, ok

router = APIRouter()

logger = logging.getLogger(__name__)

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


def _add_state_audit(db: AsyncSession, cand_row, from_state: str, to_state: str, operator: str, reason: str) -> None:
    """状态流转审计（随 PG 决策阶段先行提交——图写失败也不丢审计）。"""
    from app.models.business import AuditLog

    db.add(
        AuditLog(
            user_id=operator,
            action="discovery.state_transition",
            resource="Position",
            resource_id=cand_row.position_name,
            detail={
                "from_state": from_state,
                "to_state": to_state,
                "reason": reason,
                "seed_matched": cand_row.seed_matched,
                "rag_matched": cand_row.rag_matched,
            },
        )
    )


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
    置信度 ≥ 0.6 AND 源 ≥ 2）→ PG 决策先行（状态 + 审计 + 驳回记录一次
    提交）→ Neo4j Position.status 图写副作用最后（postmortems/003 顺序）。

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

    operator = current_user.get("sub") or current_user.get("user_id", "admin")

    # ── ① PG 决策先行（不可回滚的图写必须放在提交成功之后）：
    #    状态 + 审计 + 驳回记录一次提交，人工决策先固化
    from_state = cand_row.state
    cand_row.state = target.value
    _add_state_audit(db, cand_row, from_state, target.value, operator, reason)
    if action == "reject":
        # _persist_rejected_change 自带 commit（连同状态 + 审计一并落库）
        await _persist_rejected_change(db, cand_row.position_name, "discovery_reject", reason)
    else:
        await db.commit()

    # ── ② 图写副作用最后：失败不回滚决策，effects_applied=False 透出待对账
    effects_applied = True
    try:
        await _apply_graph_state(candidate, target, operator, reason)
    except Exception:
        logger.exception(
            "岗位审核图写副作用失败（决策已落库待对账）position=%s target=%s",
            cand_row.position_name, target.value,
        )
        effects_applied = False

    return ok(
        data={
            "id": cand_row.id,
            "position_name": cand_row.position_name,
            "state": cand_row.state,
            "reason": reason,
            "effects_applied": effects_applied,
        },
        msg=f"已{'通过晋升 emerging' if action == 'approve' else '驳回'}: {cand_row.position_name}"
        + ("" if effects_applied else "（图谱写入失败，待巡检对账）"),
    )


# 共享：图写副作用（必须在 PG 决策提交成功之后调用）

async def _apply_graph_state(candidate, target, operator: str, reason: str) -> None:
    """Neo4j Position.status 幂等写入 + 图谱热路径缓存失效（08-18 TTL 治理）。

    postmortems/003 顺序约束：本函数只承担不可回滚的图写副作用，由调用方在
    PG 决策（状态 + 审计）提交成功之后调用；失败由调用方捕获并以
    effects_applied=False 透出（人工决策不回滚，待巡检对账补图）。
    不向 machine.persist 传 db——审计已随 PG 阶段先行落库，避免重复。
    """
    await asyncio.to_thread(_write_position_state, candidate, target, operator, reason)
    from app.api.v1.graph import invalidate_graph_caches

    await invalidate_graph_caches()


def _write_position_state(candidate, target, operator: str, reason: str):
    """岗位状态图写（Neo4j 同步驱动，线程池执行避免阻塞事件循环）。"""
    from app.core.database import neo4j_driver
    from app.services.discovery.state_machine import PositionStateMachine

    machine = PositionStateMachine()
    with neo4j_driver.session() as neo4j_session:
        return machine.persist(
            neo4j_session, candidate, target, operator=operator, reason=reason,
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
    operator = current_user.get("sub") or current_user.get("user_id", "admin")
    review_reason = (req.get("reason") or "").strip() or "admin evolution review"

    # ── ① PG 决策先行（postmortems/003）：状态 + 审计 + features 修订一次提交
    from_state = cand_row.state
    cand_row.state = target.value
    _add_state_audit(db, cand_row, from_state, target.value, operator, review_reason)
    modified = req.get("modified")
    if action == "approve" and isinstance(modified, dict) and modified:
        cand_row.features = {**(cand_row.features or {}), **modified}
    await db.commit()

    # ── ② 图写副作用最后：失败不回滚决策，effects_applied=False 透出待对账
    effects_applied = True
    try:
        await _apply_graph_state(candidate, target, operator, review_reason)
    except Exception:
        logger.exception(
            "演化审核图写副作用失败（决策已落库待对账）position=%s target=%s",
            cand_row.position_name, target.value,
        )
        effects_applied = False

    return ok(
        data={
            "id": cand_row.id,
            "position_name": cand_row.position_name,
            "state": cand_row.state,
            "effects_applied": effects_applied,
        },
        msg=f"已{'确认晋级 stable' if action == 'approve' else '确认衰退 declining'}: {cand_row.position_name}"
        + ("" if effects_applied else "（图谱写入失败，待巡检对账）"),
    )


# ============================================================
# 岗位归档（[M4]：declining → archived 终态）
# ============================================================

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

    六状态机最后一环：PG 决策先行（状态 + 审计），Neo4j Position.status
    图写副作用最后（postmortems/003 顺序，reason 必填）。与
    /positions/{id}/review（candidate → emerging/rejected）和
    /evolution/{id}/review（emerging → stable/declining）并列。
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
    operator = current_user.get("sub") or current_user.get("user_id", "admin")

    # ── ① PG 决策先行：状态 + 审计一次提交
    from_state = cand_row.state
    cand_row.state = PositionState.ARCHIVED.value
    _add_state_audit(db, cand_row, from_state, PositionState.ARCHIVED.value, operator, reason)
    await db.commit()

    # ── ② 图写副作用最后：失败不回滚决策，effects_applied=False 透出待对账
    effects_applied = True
    try:
        await _apply_graph_state(candidate, PositionState.ARCHIVED, operator, reason)
    except Exception:
        logger.exception(
            "归档图写副作用失败（决策已落库待对账）position=%s", cand_row.position_name,
        )
        effects_applied = False

    return ok(
        data={
            "id": cand_row.id,
            "position_name": cand_row.position_name,
            "state": cand_row.state,
            "effects_applied": effects_applied,
        },
        msg=f"已归档（终态）: {cand_row.position_name}"
        + ("" if effects_applied else "（图谱写入失败，待巡检对账）"),
    )


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
