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

# 响应/列表字段（structured_output/evidence_refs 保留供抽检；rollback_ref 供审计）
_SERIALIZE_FIELDS = (
    "id", "domain", "entity_type", "entity_id", "run_id", "env",
    "input_hash", "evidence_refs", "provider", "model", "prompt_version",
    "schema_version", "structured_output", "confidence", "gate_result",
    "risk_tier", "status", "reviewer", "review_reason", "effects_applied",
    "rollback_ref", "duration_ms", "attempts", "fallback_reason", "created_at",
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
    """决策记录分页列表（倒序，只读；total 为过滤后总数，供分页条）。"""
    from sqlalchemy import func

    from app.core.database import async_session_factory

    async with async_session_factory() as session:
        rows = await query_decisions(session, domain, status, limit, offset)
        count_stmt = select(func.count()).select_from(LLMDecisionRecord)
        if domain:
            count_stmt = count_stmt.where(LLMDecisionRecord.domain == domain)
        if status:
            count_stmt = count_stmt.where(LLMDecisionRecord.status == status)
        total = await session.scalar(count_stmt) or 0
    return {
        "items": [serialize_record(r) for r in rows],
        "total": total, "limit": limit, "offset": offset,
    }


@router.get("/llm-decisions/summary")
async def llm_decisions_summary() -> dict:
    """决策记录汇总（domain×status，验收卡片数据源，只读）。"""
    from app.core.database import async_session_factory

    async with async_session_factory() as session:
        return await summarize(session)


# ---- 审批执行通道（PR9b + PR3 c）：proposal→approved/rejected ----

_REL_DOMAINS = {"skill_relation"}
# 图变异类域（归并/改名/关系变更 = R2 高风险动作）须人工 approve、不得 auto-apply。
# 名称归一（position/skill normalize，PR3 c）并入 _MUTABLE_DOMAINS 统一门禁。
_NORMALIZE_DOMAINS = {"position_normalize", "skill_normalize"}
# 技能分类（skill_classify）：建议晋升权威 category，同样须人工 approve
# （worker 落 shadow+R0，approve 端对 skill_classify 接受 shadow→approved）。
_MUTABLE_DOMAINS = _REL_DOMAINS | _NORMALIZE_DOMAINS | {"skill_classify"}


async def _load_decision(session, decision_id: str):
    from app.models.business import LLMDecisionRecord

    return await session.get(LLMDecisionRecord, decision_id)


def _approve_common_guard(req: dict, current_user: dict) -> tuple[dict | None, str, str]:
    """公共审批前置校验：返回 (None, reason, operator) 通过；(error_resp, "", "") 失败。

    校验 review_reason 非空 + 操作者身份为合法 UUID（AuditLog.user_id 列约束）。
    独立成函数便于单测注入，同时避免在两个 domain 分支重复。
    """
    import uuid as _uuid

    from app.core.errors import ERR_VALIDATION
    from app.schemas.common import error

    reason = (req.get("review_reason") or "").strip()
    if not reason:
        return error(ERR_VALIDATION, "审批必须填写 review_reason"), "", ""
    operator = current_user.get("sub") or current_user.get("user_id", "admin")
    try:
        _uuid.UUID(str(operator))
    except (ValueError, AttributeError, TypeError):
        return error(ERR_VALIDATION, f"操作者身份必须为 UUID（AuditLog.user_id 列约束），收到: {operator!r}"), "", ""
    return None, reason, operator


@router.post("/llm-decisions/{decision_id}/approve")
async def approve_llm_decision(
    decision_id: str,
    req: dict,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("admin:*")),
) -> dict:
    """批准一条图变异类 proposal（skill_relation / position_normalize / skill_normalize）。

    执行顺序（对齐 postmortem 003 教训）：先校验 → PG 落库（图变更持久化 +
    决策状态 approved + AuditLog）→ 成功返回。图写入由各自 sync_* 脚本幂等落图
    （与 YAML 种子并列），不在本端点内直接改 Neo4j。
    - skill_relation → scripts/sync_dynamic_relations.py
    - position/skill normalize → scripts/sync_dynamic_normalization.py
    """
    from app.core.errors import ERR_CONFLICT, ERR_NOT_FOUND, ERR_VALIDATION
    from app.schemas.common import error

    guard, reason, operator = _approve_common_guard(req, current_user)
    if guard is not None:
        return guard

    record = await _load_decision(db, decision_id)
    if record is None:
        return error(ERR_NOT_FOUND, "决策记录不存在", http_status=404)
    if record.domain not in _MUTABLE_DOMAINS:
        return error(ERR_VALIDATION, f"仅 {sorted(_MUTABLE_DOMAINS)} 域可批准（当前 {record.domain}）")
    # skill_classify 记录为 shadow（worker 验收语义），批准即晋升权威 category；
    # 其余域为 proposal 人工档。
    accepted_statuses = {"shadow"} if record.domain == "skill_classify" else {"proposal"}
    if record.status not in accepted_statuses:
        return error(ERR_CONFLICT, f"决策当前状态 {record.status}，不可批准")

    if record.domain == "skill_relation":
        return await _approve_skill_relation(db, record, reason, operator)
    if record.domain == "skill_classify":
        return await _approve_skill_classify(db, record, reason, operator)
    # 别名回写（方案①）：skill_normalize 记录且 structured_output.kind=="alias"——
    # 走 _approve_skill_alias（写 skill_aliases + reload），非归一图变异。
    if record.domain == "skill_normalize" and (record.structured_output or {}).get("kind") == "alias":
        return await _approve_skill_alias(db, record, reason, operator)
    return await _approve_normalization(db, record, reason, operator)


async def _approve_skill_relation(db, record, reason: str, operator: str) -> dict:
    """skill_relation 批准：落 SkillDynamicRelation + 决策置 approved（图写走 sync 脚本）。"""
    from app.api.common import ok
    from app.core.errors import ERR_CONFLICT, ERR_VALIDATION
    from app.models.business import AuditLog, SkillDynamicRelation
    from app.schemas.common import error
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError

    out = record.structured_output or {}
    relation_type = str(out.get("relation") or "")
    direction = str(out.get("direction") or "a_to_b")
    entity_id = str(record.entity_id or "")
    if "->" not in entity_id:
        return error(ERR_VALIDATION, "entity_id 非法（应为 源->目标）")
    source, target = [x.strip() for x in entity_id.split("->", 1)]
    if relation_type not in {"PREREQUISITE_OF", "BELONGS_TO", "ALTERNATIVE_OF"} or not source or source == target:
        return error(ERR_VALIDATION, "关系类型/方向非法（PREREQUISITE_OF/BELONGS_TO/ALTERNATIVE_OF 且源≠目标）")

    # 重复关系预查（unique(source,target,type) 会硬拒）：同对关系已被批准过
    # （如 propose 脚本重跑产生多条同对 proposal）时给出可读冲突而非 500
    existing = (await db.scalars(
        select(SkillDynamicRelation).where(
            SkillDynamicRelation.source_skill == source,
            SkillDynamicRelation.target_skill == target,
            SkillDynamicRelation.relation_type == relation_type,
        )
    )).first()
    if existing is not None:
        return error(
            ERR_CONFLICT,
            f"关系 {source}->{target}->{relation_type} 已批准过"
            f"（proposal {existing.proposal_id}），不可重复批准",
        )

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
    try:
        await db.commit()
    except IntegrityError:
        # 并发兜底：两管理员同时批准同对关系，后者撞唯一约束
        await db.rollback()
        return error(ERR_CONFLICT, "关系已存在（并发批准冲突）")
    return ok({"decision_id": str(record.id), "relation": f"{source}->{target}->{relation_type}"})


async def _approve_normalization(db, record, reason: str, operator: str) -> dict:
    """名称归一批准：落 NameNormalizationRequest + 决策置 approved（图写走 sync 脚本）。

    解析决策结构化输出为 (entity_type, action, source_name, target_name)：
    - 仅 action 为非空（即产生 rename/merge）才落 NameNormalizationRequest；
      keep_original / keep / noise 视为确认原样，不产生图变更（效果为 0）。
    - 目标名必须非空且 ≠ 源名（hard gate 已保证；此处防脏数据入表）。
    - 重复批准预查（unique(proposal_id) 硬拒）给出可读冲突而非 500。
    """
    from app.api.common import ok
    from app.core.errors import ERR_CONFLICT, ERR_VALIDATION
    from app.models.business import AuditLog, NameNormalizationRequest
    from app.schemas.common import error
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError

    from app.services.llm_decision.normalize_approval import parse_normalization

    try:
        norm = parse_normalization(record)
    except ValueError as e:
        return error(ERR_VALIDATION, str(e))

    action = norm["action"]
    source_name = norm["source_name"]
    target_name = norm["target_name"]
    entity_type = norm["entity_type"]

    # keep_original / keep / noise（action="")：确认原样，无图变更，仅置 approved
    if not action:
        record.status = "approved"
        record.reviewer = operator
        record.review_reason = reason
        record.effects_applied = True
        db.add(AuditLog(
            user_id=operator, action="llm_decision_approve",
            resource=record.domain, resource_id=str(record.id),
            detail={"source_name": source_name, "target_name": target_name,
                    "action": "noop", "reason": reason},
        ))
        await db.commit()
        return ok({"decision_id": str(record.id), "normalization": "noop",
                   "source": source_name, "target": target_name})

    if not source_name or not target_name or source_name == target_name:
        return error(ERR_VALIDATION, "名称归一变更非法（源/目标名缺失或相同）")

    # 重复批准预查（unique(proposal_id) 会硬拒）：同 proposal 已被批准过 → 可读冲突
    existing = (await db.scalars(
        select(NameNormalizationRequest).where(
            NameNormalizationRequest.proposal_id == str(record.id),
        )
    )).first()
    if existing is not None:
        return error(
            ERR_CONFLICT,
            f"记录 {source_name}→{target_name} 已批准过（proposal {existing.proposal_id}），不可重复批准",
        )

    db.add(NameNormalizationRequest(
        entity_type=entity_type, action=action,
        source_name=source_name, target_name=target_name,
        primary_node_name=norm["primary_node_name"],
        proposal_id=str(record.id), reviewed_by=operator, review_reason=reason,
    ))
    record.status = "approved"
    record.reviewer = operator
    record.review_reason = reason
    record.effects_applied = True
    db.add(AuditLog(
        user_id=operator, action="llm_decision_approve",
        resource=record.domain, resource_id=str(record.id),
        detail={"entity_type": entity_type, "action": action,
                "source": source_name, "target": target_name, "reason": reason},
    ))
    try:
        await db.commit()
    except IntegrityError:
        # 并发兜底：两管理员同时批准同一 proposal，后者撞唯一约束
        await db.rollback()
        return error(ERR_CONFLICT, "该名称归一已批准（并发批准冲突）")
    return ok({"decision_id": str(record.id), "normalization": f"{action}:{source_name}->{target_name}",
               "source": source_name, "target": target_name})


async def _approve_skill_classify(db, record, reason: str, operator: str) -> dict:
    """技能分类批准：落 SkillCategoryApproval + 决策置 approved（图写走 sync 脚本）。

    worker 已把 LLM 建议写进图谱 `suggested_category*` 提议字段（不动权威
    category），approve 在此固化批准意图（PG），由
    scripts/sync_dynamic_categories.py 把 Skill.category 晋升为批准值。
    category 必须非空（classify_skill 侧 KNOWN_CATEGORIES 枚举已保证）。
    """
    from app.api.common import ok
    from app.core.errors import ERR_CONFLICT, ERR_VALIDATION
    from app.models.business import AuditLog, SkillCategoryApproval
    from app.schemas.common import error
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError

    skill_name = str(record.entity_id or "").strip()
    category = str((record.structured_output or {}).get("category") or "").strip()
    if not skill_name or not category:
        return error(ERR_VALIDATION, "技能分类审批非法（技能名/category 缺失）")

    # 重复批准预查（unique(proposal_id) 硬拒）：同 proposal 已被批准过 → 可读冲突
    existing = (await db.scalars(
        select(SkillCategoryApproval).where(
            SkillCategoryApproval.proposal_id == str(record.id),
        )
    )).first()
    if existing is not None:
        return error(
            ERR_CONFLICT,
            f"技能 {skill_name}→{category} 已批准过（proposal {existing.proposal_id}），不可重复批准",
        )

    db.add(SkillCategoryApproval(
        skill_name=skill_name, category=category,
        proposal_id=str(record.id), reviewed_by=operator, review_reason=reason,
    ))
    record.status = "approved"
    record.reviewer = operator
    record.review_reason = reason
    record.effects_applied = True
    db.add(AuditLog(
        user_id=operator, action="llm_decision_approve",
        resource=record.domain, resource_id=str(record.id),
        detail={"skill_name": skill_name, "category": category, "reason": reason},
    ))
    try:
        await db.commit()
    except IntegrityError:
        # 并发兜底：两管理员同时批准同一 proposal，后者撞唯一约束
        await db.rollback()
        return error(ERR_CONFLICT, "该技能分类已批准（并发批准冲突）")
    return ok({"decision_id": str(record.id), "category": category, "skill": skill_name})


async def _approve_skill_alias(db, record, reason: str, operator: str) -> dict:
    """技能别名回写批准（方案①）：写 skill_aliases(approved) + reload_dynamic_aliases。

    skill_normalize 记录且 structured_output.kind=="alias"（propose_skill_alias 产出）：
    approve 即把该"别名→标准名"写进 skill_aliases（status=approved），供
    normalize_skill 并查（词典→动态→白名单）。standard_name 必须命中
    known_standard_names（propose 侧 gate 已保证；此处二次校验防脏数据）。
    图边界：别名回写不改图谱拓扑（非图变异），故落 skill_aliases 足够。
    """
    from app.api.common import ok
    from app.core.errors import ERR_CONFLICT, ERR_VALIDATION
    from app.models.business import AuditLog, SkillAlias
    from app.schemas.common import error
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError

    alias = str(record.entity_id or "").strip()
    out = record.structured_output or {}
    standard = str(out.get("target_standard") or "").strip()
    confidence = out.get("confidence")
    if not alias or not standard:
        return error(ERR_VALIDATION, "别名回写审批非法（variant/target_standard 缺失）")
    # gate 二次校验：standard 必须命中权威标准名（`known_standard_names()`）
    from app.services.llm_decision.skill_normalize import known_standard_names

    if standard not in known_standard_names():
        return error(ERR_VALIDATION, f"标准名 {standard!r} 不在权威标准名集合（防虚构）")

    # 幂等：unique(variant) —— 已存在该 variant（pending/approved）则跳过
    existing = (await db.scalars(
        select(SkillAlias).where(SkillAlias.variant == alias)
    )).first()
    if existing is not None:
        return error(ERR_CONFLICT, f"别名 {alias!r} 已存在（proposal {existing.proposal_id}），不可重复批准")

    db.add(SkillAlias(
        variant=alias, standard_name=standard,
        status="approved", proposal_id=str(record.id),
        reviewed_by=operator, review_reason=reason, confidence=confidence,
    ))
    record.status = "approved"
    record.reviewer = operator
    record.review_reason = reason
    record.effects_applied = True
    db.add(AuditLog(
        user_id=operator, action="llm_decision_approve",
        resource=record.domain, resource_id=str(record.id),
        detail={"kind": "alias", "variant": alias, "standard": standard, "reason": reason},
    ))
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        return error(ERR_CONFLICT, "该别名已批准（并发批准冲突）")
    # 触发 normalize_skill 动态别名表刷新（approve 后立即可并查）
    try:
        from app.services.extraction.dictionary import reload_dynamic_aliases
        reload_dynamic_aliases()
    except Exception:
        pass  # reload 失败（DB 抖动）不阻断；下次启动会重新加载
    return ok({"decision_id": str(record.id), "variant": alias, "standard": standard})


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
    # skill_classify 记录为 shadow（worker 验收语义），其余域为 proposal 人工档。
    accepted_statuses = {"shadow"} if record.domain == "skill_classify" else {"proposal"}
    if record.status not in accepted_statuses:
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