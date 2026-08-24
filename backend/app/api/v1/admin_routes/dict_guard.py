"""管理后台字典守卫域路由：提案审核 / 变更审计与回滚 / 巡检报告（RBAC admin only）。

对齐契约 /api/v1/admin/dict-guard/*（技能字典自治守卫方案 §7）。approve 执行
语义（§5 风险不对称原则）：动态过滤层操作即时生效；**静态停用词的 remove 以
受影响技能的动态 protect 落地**（git 词表走固化流程，运行时不动）；全部变更写
DictChangeLog(source="manual"/"rollback") + AuditLog，回滚按记录反向操作并防复滚。
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.common import iso, ok, paged_ok, paginate
from app.api.deps import require_permission
from app.core.database import get_db
from app.core.errors import ERR_CONFLICT, ERR_INTERNAL, ERR_NOT_FOUND, ERR_VALIDATION
from app.models.business import AuditLog, DictChangeLog, DictProposal
from app.schemas.common import error
from app.services.extraction import dynamic_filters as dyn
from app.services.extraction.dictionary import SKILL_STOPWORDS

router = APIRouter()

logger = logging.getLogger(__name__)

# 巡检报告目录（backend/reports，与 workers/dict_guard.py 写入侧同约定；
# 模块级便于测试注入 tmp_path）
_REPORT_DIR = Path(__file__).resolve().parents[4] / "reports"


def _cleanup_skill_nodes(term: str) -> int:
    """scoped 清理：删除与停用词同名的 Skill 节点（与 auto 路径 workers/dict_guard 同语义）。"""
    from app.core.database import neo4j_driver

    with neo4j_driver.session() as session:
        record = session.run(
            "MATCH (s:Skill {name: $term}) DETACH DELETE s RETURN count(s) AS n",
            term=term,
        ).single()
        return record["n"] if record else 0


def _cleanup_position_node(term: str) -> int:
    """删除脏岗位节点（DETACH 连带 REQUIRES 等边）。"""
    from app.core.database import neo4j_driver

    with neo4j_driver.session() as session:
        record = session.run(
            "MATCH (p:Position {name: $term}) DETACH DELETE p RETURN count(p) AS n",
            term=term,
        ).single()
        return record["n"] if record else 0


def _cleanup_course_node(term: str) -> int:
    """删除孤立脏课程节点（DETACH 连带 LEARNABLE_VIA 等边）。"""
    from app.core.database import neo4j_driver

    with neo4j_driver.session() as session:
        record = session.run(
            "MATCH (c:Course {name: $term}) DETACH DELETE c RETURN count(c) AS n",
            term=term,
        ).single()
        return record["n"] if record else 0


def _cleanup_course_edge(term: str) -> int:
    """删除课程脏边『技能→课程』（LEARNABLE_VIA，不删课程节点）。"""
    from app.core.database import neo4j_driver

    source, target = term.split("→", 1) if "→" in term else (term, "")
    with neo4j_driver.session() as session:
        record = session.run(
            "MATCH (s:Skill {name: $source})-[r:LEARNABLE_VIA]->(c:Course {name: $target}) "
            "DELETE r RETURN count(r) AS n",
            source=source, target=target,
        ).single()
        return record["n"] if record else 0


def _cleanup_by_proposal(row) -> tuple[str, int]:
    """按提案 action/entity_type 分派清理动作；返回 (kind, 受影响单元数)。"""
    if row.action == "add_stopword":
        return "blocked", _cleanup_skill_nodes(row.term)
    if row.action == "remove_node":
        if row.entity_type == "position":
            return "node", _cleanup_position_node(row.term)
        return "node", _cleanup_course_node(row.term)
    if row.action == "remove_edge":
        return "edge", _cleanup_course_edge(row.term)
    return "blocked", 0


def _victim_of(evidence: list | dict | None) -> str:
    """从提案证据解析「受影响技能」（停用词误杀检测写入的成对证据）。"""
    items = evidence if isinstance(evidence, list) else []
    for e in items:
        if isinstance(e, dict) and e.get("label") == "受影响技能":
            return str(e.get("value") or "").strip()
    return ""


@router.get("/dict-guard/proposals")
async def list_proposals(
    status: str = Query(default="pending", pattern="^(pending|approved|rejected)$"),
    entity_type: str = Query(default="", pattern="^(skill|position|course)?$"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """字典守卫待审提案列表（默认 pending——审核池待办口径）。"""
    stmt = select(DictProposal)
    count_stmt = select(func.count()).select_from(DictProposal)
    if status:
        stmt = stmt.where(DictProposal.status == status)
        count_stmt = count_stmt.where(DictProposal.status == status)
    if entity_type:
        stmt = stmt.where(DictProposal.entity_type == entity_type)
        count_stmt = count_stmt.where(DictProposal.entity_type == entity_type)
    rows, total = await paginate(
        db, stmt.order_by(DictProposal.created_at.desc()), page, size,
        count_stmt=count_stmt,
    )
    return paged_ok(
        [
            {
                "id": r.id,
                "entity_type": r.entity_type,
                "term": r.term,
                "action": r.action,
                "status": r.status,
                "reason": r.reason,
                "llm_confidence": r.llm_confidence,
                "evidence": r.evidence or [],
                "impact_stats": r.impact_stats or {},
                "run_date": r.run_date,
                "reviewed_by": r.reviewed_by,
                "review_reason": r.review_reason,
                "reviewed_at": iso(r.reviewed_at),
                "created_at": iso(r.created_at),
            }
            for r in rows
        ],
        total=total, page=page, size=size,
    )


@router.post("/dict-guard/proposals/{proposal_id}/review")
async def review_proposal(
    proposal_id: str,
    req: dict,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("admin:*")),
):
    """审核提案：approve 按动作执行动态层变更 / reject 仅置状态。

    执行顺序（20260824 事故整改，见 postmortems/003）：**先校验、再落库、
    最后执行副作用**——动态词表与 Neo4j 删除不可回滚，必须放在 PG 提交成功
    之后；此前删除先行、提交在后，中途失败（如 AuditLog 的 UUID 校验）会
    产生「图谱已删而提案仍 pending」的半执行态。

    approve 执行语义（方案 §5）：
    - add_stopword：动态 blocked 即时生效 + scoped 清理同名 Skill（与 auto 一致）
    - remove_stopword：动态条目直接移除；静态停用词以「受影响技能」的动态
      protect 落地（git 词表走固化流程），证据缺失时拒绝并提示改用 protect
    - protect_whitelist：为具体技能名加动态保护
    - remove_node/remove_edge（position/course）：直接删除对应岗位/课程节点或
      课程脏边（无动态层变更），图谱删除不可回滚，approved 后立即生效
    """
    action = req.get("action")
    reason = (req.get("reason") or "").strip()
    if action not in ("approve", "reject"):
        return error(ERR_VALIDATION, "action 必须为 approve 或 reject")
    if not reason:
        return error(ERR_VALIDATION, "审核必须填写 reason")

    row = await db.get(DictProposal, proposal_id)
    if row is None:
        return error(ERR_NOT_FOUND, "提案不存在", http_status=404)
    if row.status != "pending":
        return error(ERR_CONFLICT, f"提案当前状态 {row.status}，不可审核")

    operator = current_user.get("sub") or current_user.get("user_id", "admin")
    try:
        uuid.UUID(str(operator))
    except (ValueError, AttributeError, TypeError):
        return error(ERR_VALIDATION, f"操作者身份必须为 UUID（AuditLog.user_id 列约束），收到: {operator!r}")

    effect_term = row.term
    changelog_row = None

    if action == "approve":
        # 预解析效果类型（只读判定，不产生任何副作用）
        if row.action == "add_stopword":
            effect_kind = "blocked"
        elif row.action == "remove_stopword":
            if dyn.is_dynamically_blocked(row.term):
                effect_kind = "blocked"
            elif row.term in SKILL_STOPWORDS:
                victim = _victim_of(row.evidence)
                if not victim:
                    return error(
                        ERR_CONFLICT,
                        "静态停用词移除需走 git 固化流程；请在提案证据中指明受影响技能"
                        "后改用 protect_whitelist",
                    )
                # 静态词不动，保护受影响的真实技能使其穿透停用词
                effect_kind = "protected"
                effect_term = victim
            else:
                return error(ERR_CONFLICT, "目标不是现行停用词，无需移除")
        elif row.action == "protect_whitelist":
            effect_kind = "protected"
        elif row.action in ("remove_node", "remove_edge"):
            if row.entity_type not in ("position", "course"):
                return error(ERR_CONFLICT, "图谱删除提案的 entity_type 必须为 position/course")
            effect_kind = "node" if row.action == "remove_node" else "edge"
        else:
            return error(ERR_CONFLICT, f"未知提案动作: {row.action}")

    row.status = "approved" if action == "approve" else "rejected"
    row.reviewed_by = operator
    row.review_reason = reason
    row.reviewed_at = datetime.now(timezone(timedelta(hours=8)))

    if action == "approve":
        changelog_row = DictChangeLog(
            term=effect_term, action=row.action, source="manual", kind=effect_kind,
            proposal_id=row.id, reason=reason, entity_type=getattr(row, "entity_type", "skill"),
            detail={"operator": operator},
            impact_stats=row.impact_stats or {},
        )
        db.add(changelog_row)
    db.add(AuditLog(
        user_id=operator,
        action=f"admin.dict_guard.{action}",
        resource="dict_proposal",
        resource_id=row.id,
        detail={"term": row.term, "proposal_action": row.action, "reason": reason},
    ))
    await db.commit()

    # ── 副作用阶段（不可回滚操作放最后）：失败不回滚已批准的状态，以日志 +
    #    effects_applied=False 透出，由巡检报告对账兜底
    if action == "approve":
        try:
            if row.action == "add_stopword":
                dyn.add_entry("blocked", row.term, reason=reason, source="dict_guard_review")
                removed = await asyncio.to_thread(_cleanup_skill_nodes, row.term)
                row.impact_stats = {**(row.impact_stats or {}), "removed_nodes": removed}
            elif row.action == "remove_stopword":
                if effect_kind == "blocked":
                    dyn.remove_entry("blocked", row.term)
                else:
                    dyn.add_entry("protected", effect_term, reason=reason, source="dict_guard_review")
            elif row.action == "protect_whitelist":
                dyn.add_entry("protected", row.term, reason=reason, source="dict_guard_review")
            else:  # remove_node / remove_edge
                _, removed = await asyncio.to_thread(_cleanup_by_proposal, row)
                row.impact_stats = {
                    **(row.impact_stats or {}), "removed_units": removed, "kind": effect_kind,
                }
            if changelog_row is not None:
                # session 内持久对象直接改属性即脏标记，无需重复 add
                changelog_row.impact_stats = row.impact_stats or {}
            await db.commit()
        except Exception:
            logger.exception("dict_guard 审批副作用执行失败 proposal=%s term=%s", row.id, row.term)
            return ok(
                data={
                    "id": row.id, "term": row.term, "action": row.action,
                    "status": row.status, "effects_applied": False,
                },
                msg=f"已批准但副作用执行失败（详见服务日志，待巡检对账）: {row.term}",
            )

    return ok(
        data={"id": row.id, "term": row.term, "action": row.action, "status": row.status},
        msg=f"已{'批准执行' if action == 'approve' else '驳回'}: {row.term}",
    )


@router.get("/dict-guard/changes")
async def list_changes(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """字典变更审计列表（auto/manual/rollback 全量，倒序）。"""
    stmt = select(DictChangeLog).order_by(DictChangeLog.created_at.desc())
    count_stmt = select(func.count()).select_from(DictChangeLog)
    rows, total = await paginate(db, stmt, page, size, count_stmt=count_stmt)
    return paged_ok(
        [
            {
                "id": r.id,
                "entity_type": r.entity_type,
                "term": r.term,
                "action": r.action,
                "source": r.source,
                "kind": r.kind,
                "proposal_id": r.proposal_id,
                "reason": r.reason,
                "detail": r.detail or {},
                "impact_stats": r.impact_stats or {},
                "applied_by": r.applied_by,
                "created_at": iso(r.created_at),
            }
            for r in rows
        ],
        total=total, page=page, size=size,
    )


@router.post("/dict-guard/changes/{change_id}/rollback")
async def rollback_change(
    change_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("admin:*")),
):
    """回滚变更：blocked→移除 / protected→解除，反向写 source=rollback 审计。"""
    row = await db.get(DictChangeLog, change_id)
    if row is None:
        return error(ERR_NOT_FOUND, "变更记录不存在", http_status=404)
    if row.action == "rollback":
        return error(ERR_CONFLICT, "回滚记录不可再次回滚")
    re_rolled = await db.scalar(
        select(func.count()).select_from(DictChangeLog).where(
            DictChangeLog.source == "rollback",
            DictChangeLog.detail["original_id"].astext == row.id,
        )
    )
    if re_rolled:
        return error(ERR_CONFLICT, "该变更已回滚过（防复滚）")

    operator = current_user.get("sub") or current_user.get("user_id", "admin")
    try:
        uuid.UUID(str(operator))
    except (ValueError, AttributeError, TypeError):
        return error(ERR_VALIDATION, f"操作者身份必须为 UUID（AuditLog.user_id 列约束），收到: {operator!r}")
    if row.kind == "blocked":
        removed = dyn.remove_entry("blocked", row.term)
        if not removed:
            return error(ERR_CONFLICT, "动态停用词中已无该词条（可能已被移除）")
    elif row.kind == "protected":
        removed = dyn.remove_entry("protected", row.term)
        if not removed:
            return error(ERR_CONFLICT, "动态保护中已无该词条（可能已被解除）")
    else:
        return error(ERR_CONFLICT, f"未知条目类型 {row.kind}，无法回滚")

    db.add(DictChangeLog(
        term=row.term, action="rollback", source="rollback", kind=row.kind,
        proposal_id=row.proposal_id, reason=f"回滚变更 {row.id}",
        detail={"original_id": row.id, "operator": operator},
        impact_stats=row.impact_stats or {}, applied_by=operator,
    ))
    db.add(AuditLog(
        user_id=operator,
        action="admin.dict_guard.rollback",
        resource="dict_change_log",
        resource_id=row.id,
        detail={"term": row.term, "kind": row.kind},
    ))
    await db.commit()

    return ok(data={"id": row.id, "term": row.term, "kind": row.kind}, msg=f"已回滚: {row.term}")


@router.get("/dict-guard/report/latest")
async def latest_report():
    """最近一次字典守卫巡检报告（reports/dict_guard_{date}.json 取最新）。"""
    files = sorted(_REPORT_DIR.glob("dict_guard_*.json")) if _REPORT_DIR.exists() else []
    if not files:
        return error(ERR_NOT_FOUND, "暂无巡检报告", http_status=404)
    return ok(data=json.loads(files[-1].read_text(encoding="utf-8")))


@router.post("/dict-guard/trigger", status_code=202)
async def trigger_dict_guard(
    current_user: dict = Depends(require_permission("admin:*")),
):
    """手动立即触发一次字典守卫巡检（复跑 ETL 阶段 16 的 dict_guard_daily）。

    前端「手动巡检」开关入口；入队到 ARQ（依赖 worker 消费）。运行结果落
    reports/dict_guard_{date}.json，可在面板刷新查看，非实时阻塞。
    """
    from app.core.arq_client import enqueue

    try:
        await enqueue("dict_guard_daily")
    except Exception:
        logger.exception("字典守卫手动触发入队失败")
        return error(ERR_INTERNAL, "任务入队失败，请稍后重试")
    logger.info("字典守卫手动触发已入队 operator=%s", current_user.get("sub", "admin"))
    return ok(msg="字典守卫巡检已提交，等待 worker 执行")
