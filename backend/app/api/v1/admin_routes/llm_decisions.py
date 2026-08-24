"""LLM 决策与验收只读接口（PR7：管理后台决策页数据源）。

- GET /admin/llm-decisions：决策记录分页列表（domain/status 过滤，倒序）
- GET /admin/llm-decisions/summary：按 domain×status 汇总（验收卡片）

只读，不触发任何写操作（决策记录的生产者见各域 worker/scripts）。
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_permission
from app.core.database import get_db
from app.models.business import LLMDecisionRecord

router = APIRouter(tags=["admin-llm-decisions"])

# 响应/列表字段（避免把内部大字段无谓外泄；structured_output 保留供抽检）
_SERIALIZE_FIELDS = (
    "id", "domain", "entity_type", "entity_id", "run_id", "env",
    "input_hash", "provider", "model", "prompt_version", "schema_version",
    "structured_output", "confidence", "gate_result", "risk_tier", "status",
    "reviewer", "review_reason", "effects_applied", "duration_ms",
    "attempts", "fallback_reason", "created_at",
)


def serialize_record(record: LLMDecisionRecord) -> dict:
    """ORM 记录 → 契约字段（created_at 转 ISO 字符串，JSON 友好）。"""
    data: dict = {}
    for field in _SERIALIZE_FIELDS:
        value = getattr(record, field, None)
        if field == "created_at" and value is not None:
            value = value.isoformat()
        data[field] = value
    return data


def build_query(domain: str, status: str, limit: int, offset: int):
    """决策列表查询（纯函数可测）：domain/status 过滤 + 倒序分页。"""
    stmt = select(LLMDecisionRecord).order_by(LLMDecisionRecord.created_at.desc())
    if domain:
        stmt = stmt.where(LLMDecisionRecord.domain == domain)
    if status:
        stmt = stmt.where(LLMDecisionRecord.status == status)
    return stmt.limit(limit).offset(offset)


async def query_decisions(
    session: AsyncSession,
    domain: str = "",
    status: str = "",
    limit: int = 50,
    offset: int = 0,
) -> list[LLMDecisionRecord]:
    """执行决策列表查询（session 由调用方提供，便于测试注入）。"""
    rows = await session.scalars(build_query(domain, status, limit, offset))
    return list(rows.all())


async def summarize(session: AsyncSession) -> dict:
    """按 domain×status 汇总（记录量小，内存聚合；供管理端卡片）。"""
    rows = (await session.scalars(select(LLMDecisionRecord))).all()
    by_domain: dict[str, dict] = {}
    totals: dict[str, int] = {"proposal": 0, "auto_applied": 0, "blocked": 0, "shadow": 0, "other": 0}
    for r in rows:
        domain_entry = by_domain.setdefault(r.domain, {"domain": r.domain, "by_status": {}, "total": 0})
        domain_entry["by_status"][r.status] = domain_entry["by_status"].get(r.status, 0) + 1
        domain_entry["total"] += 1
        key = r.status if r.status in totals else "other"
        totals[key] += 1
    totals["records"] = len(rows)
    return {
        "by_domain": sorted(by_domain.values(), key=lambda d: -d["total"]),
        "totals": totals,
    }


@router.get("/llm-decisions")
async def list_llm_decisions(
    domain: str = Query(default="", description="决策域过滤（空=全部）"),
    status: str = Query(default="", description="状态过滤：shadow/proposal/auto_applied/blocked 等（空=全部）"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """决策记录分页列表（倒序，只读）。"""
    from app.core.database import async_session_factory

    async with async_session_factory() as session:
        rows = await query_decisions(session, domain, status, limit, offset)
    return {"items": [serialize_record(r) for r in rows], "limit": limit, "offset": offset}


@router.get("/llm-decisions/summary")
async def llm_decisions_summary() -> dict:
    """决策记录汇总（domain×status，验收卡片数据源，只读）。"""
    from app.core.database import async_session_factory

    async with async_session_factory() as session:
        return await summarize(session)


# ---- 技能关系审批执行通道（PR9b）：proposal→approved/rejected ----

_REL_DOMAINS = {"skill_relation"}


async def _load_decision(session, decision_id: str):
    from app.models.business import LLMDecisionRecord

    return await session.get(LLMDecisionRecord, decision_id)


@router.post("/llm-decisions/{decision_id}/approve")
async def approve_llm_decision(
    decision_id: str,
    req: dict,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("admin:*")),
) -> dict:
    """批准一条 skill_relation proposal：落动态关系表 + 决策记录置 approved。

    执行顺序（对齐 postmortem 003 教训）：先校验 → PG 落库（动态关系 +
    决策状态 + AuditLog）→ 成功返回。图写入由 scripts/sync_dynamic_relations.py
    幂等 MERGE（与 YAML 种子并列），不在本端点内直接改 Neo4j。
    """
    import uuid as _uuid

    from app.api.common import ok
    from app.core.errors import ERR_CONFLICT, ERR_NOT_FOUND, ERR_VALIDATION
    from app.models.business import AuditLog, SkillDynamicRelation
    from app.schemas.common import error

    reason = (req.get("review_reason") or "").strip()
    if not reason:
        return error(ERR_VALIDATION, "审批必须填写 review_reason")
    operator = current_user.get("sub") or current_user.get("user_id", "admin")
    try:
        _uuid.UUID(str(operator))
    except (ValueError, AttributeError, TypeError):
        return error(ERR_VALIDATION, f"操作者身份必须为 UUID（AuditLog.user_id 列约束），收到: {operator!r}")

    record = await _load_decision(db, decision_id)
    if record is None:
        return error(ERR_NOT_FOUND, "决策记录不存在", http_status=404)
    if record.domain not in _REL_DOMAINS:
        return error(ERR_VALIDATION, f"仅 skill_relation 域可批准（当前 {record.domain}）")
    if record.status != "proposal":
        return error(ERR_CONFLICT, f"决策当前状态 {record.status}，不可批准")

    out = record.structured_output or {}
    relation_type = str(out.get("relation") or "")
    direction = str(out.get("direction") or "a_to_b")
    entity_id = str(record.entity_id or "")
    if "->" not in entity_id:
        return error(ERR_VALIDATION, "entity_id 非法（应为 源->目标）")
    source, target = [x.strip() for x in entity_id.split("->", 1)]
    if relation_type not in {"PREREQUISITE_OF", "BELONGS_TO", "ALTERNATIVE_OF"} or not source or source == target:
        return error(ERR_VALIDATION, "关系类型/方向非法（PREREQUISITE_OF/BELONGS_TO/ALTERNATIVE_OF 且源≠目标）")

    db.add(SkillDynamicRelation(
        source_skill=source, target_skill=target, relation_type=relation_type,
        direction=direction, proposal_id=str(record.id), reviewed_by=operator,
        review_reason=reason,
    ))
    record.status = "approved"
    record.reviewer = operator
    record.review_reason = reason
    record.effects_applied = True
    db.add(AuditLog(
        user_id=operator, action="llm_decision_approve",
        resource="skill_relation", resource_id=str(record.id),
        detail={"source": source, "target": target, "relation_type": relation_type,
                "reason": reason},
    ))
    await db.commit()
    return ok({"decision_id": str(record.id), "relation": f"{source}->{target}->{relation_type}"})


@router.post("/llm-decisions/{decision_id}/reject")
async def reject_llm_decision(
    decision_id: str,
    req: dict,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("admin:*")),
) -> dict:
    """驳回一条 proposal（仅 status 流转，效果为 0）。"""
    import uuid as _uuid

    from app.api.common import ok
    from app.core.errors import ERR_CONFLICT, ERR_NOT_FOUND, ERR_VALIDATION
    from app.models.business import AuditLog
    from app.schemas.common import error

    reason = (req.get("review_reason") or "").strip()
    if not reason:
        return error(ERR_VALIDATION, "审批必须填写 review_reason")
    operator = current_user.get("sub") or current_user.get("user_id", "admin")
    try:
        _uuid.UUID(str(operator))
    except (ValueError, AttributeError, TypeError):
        return error(ERR_VALIDATION, f"操作者身份必须为 UUID（AuditLog.user_id 列约束），收到: {operator!r}")

    record = await _load_decision(db, decision_id)
    if record is None:
        return error(ERR_NOT_FOUND, "决策记录不存在", http_status=404)
    if record.status != "proposal":
        return error(ERR_CONFLICT, f"决策当前状态 {record.status}，不可驳回")

    record.status = "rejected"
    record.reviewer = operator
    record.review_reason = reason
    db.add(AuditLog(
        user_id=operator, action="llm_decision_reject",
        resource=record.domain, resource_id=str(record.id),
        detail={"reason": reason},
    ))
    await db.commit()
    return ok({"decision_id": str(record.id)})