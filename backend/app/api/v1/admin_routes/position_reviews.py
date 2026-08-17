"""管理后台岗位审核域路由：候选池审核 / 演化审核 / 归档 / 技术观察池（RBAC admin only）。

对齐契约 /api/v1/admin/{positions,evolution,discovery}/*。review 走六状态机
（PositionStateMachine）校验 + Neo4j Position.status 同步 + 审计日志；
_persist_position_state 为本域共享的 Neo4j 持久化 helper（线程池执行）。
"""

import asyncio

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


# 共享：岗位状态持久化（Neo4j 同步驱动 + 审计日志，线程池执行）

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
