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
from app.schemas.admin_requests import LLMDecisionReviewRequest

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
):
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
    # ApiResponse 包装对齐契约（第六轮审查 P0-2 同类：此前裸返，前端 apiGet
    # 取 res.data.data 得 undefined——决策页列表/汇总是 P0-2 漏网的同源断页）
    from app.api.common import ok

    return ok({
        "items": [serialize_record(r) for r in rows],
        "total": total, "limit": limit, "offset": offset,
    })


@router.get("/llm-decisions/summary")
async def llm_decisions_summary():
    """决策记录汇总（domain×status，验收卡片数据源，只读）。"""
    from app.api.common import ok
    from app.core.database import async_session_factory

    async with async_session_factory() as session:
        # ApiResponse 包装对齐契约（同上）
        return ok(await summarize(session))


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


def _approve_common_guard(reason_raw: str, current_user: dict) -> tuple[dict | None, str, str]:
    """公共审批前置校验：返回 (None, reason, operator) 通过；(error_resp, "", "") 失败。

    review_reason 非空已由 LLMDecisionReviewRequest Pydantic 强校验（P1-4），
    此处仅做 strip 后非空复核 + 操作者 UUID 守卫（AuditLog.user_id 列约束）。
    独立成函数便于单测注入，同时避免在两个 domain 分支重复。
    """
    from app.api.common import resolve_operator
    from app.core.errors import ERR_VALIDATION
    from app.schemas.common import error

    reason = (reason_raw or "").strip()
    if not reason:
        return error(ERR_VALIDATION, "审批必须填写 review_reason"), "", ""
    operator, operator_err = resolve_operator(current_user)
    if operator_err is not None:
        return operator_err, "", ""
    return None, reason, operator


@router.post("/llm-decisions/{decision_id}/approve")
async def approve_llm_decision(
    decision_id: str,
    req: LLMDecisionReviewRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("admin:*")),
):
    """批准一条图变异类 proposal（skill_relation / position_normalize / skill_normalize）。

    执行顺序（对齐 postmortem 003 教训）：先校验 → PG 落库（图变更持久化 +
    决策状态 approved + AuditLog）→ 成功返回。图写入由各自 sync_* 脚本幂等落图
    （与 YAML 种子并列），不在本端点内直接改 Neo4j。
    - skill_relation → scripts/sync_dynamic_relations.py
    - position/skill normalize → scripts/sync_dynamic_normalization.py
    """
    from app.core.errors import ERR_CONFLICT, ERR_NOT_FOUND, ERR_VALIDATION
    from app.schemas.common import error

    guard, reason, operator = _approve_common_guard(req.review_reason, current_user)
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
    # 图写由 sync_dynamic_relations 执行——approve 置 False 待落图，sync 按
    # proposal_id 回写 True（#570 对账语义，第六轮审查此前恒 True 名不副实）
    record.effects_applied = False
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
    # 图写由 sync_dynamic_normalization 执行——approve 置 False 待落图
    # （noop/keep 分支无图变更，仍在上文置 True）
    record.effects_applied = False
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
    # 图写由 sync_dynamic_categories 执行——approve 置 False 待落图（同上）
    record.effects_applied = False
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
    """技能别名回写批准：落/更新 skill_aliases(approved) + 刷新动态别名缓存。

    skill_normalize 记录且 structured_output.kind=="alias"（propose_skill_alias 产出）：
    approve 即把该"别名→标准名"落 skill_aliases（status=approved），供
    normalize_skill 并查（词典→动态→白名单）。standard_name 必须命中
    known_standard_names（propose 侧 gate 已保证；此处二次校验防脏数据）。
    幂等（unique(variant)）：同名 pending 待审行升级为 approved（方案 A 打通
    「技能治理→别名复核」的双轨），已生效/已驳回则拒绝；无行时才新建。
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

    # 幂等：unique(variant)——同名已有行一律不重复新建；已生效/已驳回则拒绝，
    # 仅对 pending 待审行执行"批准"（写入 proposal_id/review 信息，套 upsurge到 approved）。
    existing = (await db.scalars(
        select(SkillAlias).where(SkillAlias.variant == alias)
    )).first()
    if existing is not None:
        if existing.status == "approved":
            return error(ERR_CONFLICT, f"别名 {alias!r} 已生效，不可重复批准")
        if existing.status == "rejected":
            return error(ERR_CONFLICT, f"别名 {alias!r} 已驳回，不可批准")
        # pending 待审行：本决策页批准等价"复核通过"，更新为 approved
        existing.status = "approved"
        existing.reviewed_by = operator
        existing.review_reason = reason
        existing.confidence = confidence
        existing.proposal_id = str(record.id)
        db.add(existing)
    else:
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
    # 触发 normalize_skill 动态别名表刷新：API 进程即时生效；worker 进程由
    # on_startup + 每轮 ETL 起点刷新兜底（跨进程按轮次生效——第六轮审查 P0-1，
    # 原 reload_dynamic_aliases 在事件循环内 asyncio.run 必抛且被静默吞掉）
    from app.services.extraction.dictionary import refresh_dynamic_aliases

    await refresh_dynamic_aliases()
    return ok({"decision_id": str(record.id), "variant": alias, "standard": standard})


@router.post("/llm-decisions/{decision_id}/reject")
async def reject_llm_decision(
    decision_id: str,
    req: LLMDecisionReviewRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("admin:*")),
):
    """驳回一条 proposal（仅 status 流转，效果为 0）。"""
    from app.api.common import ok, resolve_operator
    from app.core.errors import ERR_CONFLICT, ERR_NOT_FOUND, ERR_VALIDATION
    from app.models.business import AuditLog
    from app.schemas.common import error

    reason = req.review_reason.strip()
    if not reason:
        return error(ERR_VALIDATION, "审批必须填写 review_reason")
    operator, operator_err = resolve_operator(current_user)
    if operator_err is not None:
        return operator_err

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

# ---- 治理救济通道：auto_applied 撤销（2026-08-31 方案 A） ----
# 此前 dict_guard 自动档（低影响+高置信的 add_stopword/remove_node/remove_edge）
# 生效后无任何事后救济：auto_applied 不接受 approve/reject，误删只能重建图。
# 撤销 = 反做副作用（移除动态过滤 / 按 course_raw 重建课程节点）+ status=reverted
# （dict_guard 据此对同实体+同动作永久跳过自动档，防止次日 ETL 再删）。

# 撤销时间窗（天）：超窗记录只能走重爬/重建等其他恢复路径
_UNDO_WINDOW_DAYS = 7
# 可反做的自动动作（与 dict_guard tier_for 自动白名单对应）
_UNDOABLE_ACTIONS = {"add_stopword", "remove_node", "remove_edge"}


async def _rebuild_course_nodes(term: str) -> int:
    """按 course_raw 同名快照重建课程节点与 LEARNABLE_VIA 边。

    PG 拉取走事件循环，Neo4j 同步写入放线程池（ARQ/FastAPI 心跳保护惯例）。
    返回重建节点数；0 = course_raw 已无同名记录（重爬后由 load_courses 自然恢复）。
    """
    import asyncio

    from app.core.database import async_session_factory, neo4j_driver
    from app.models.raw import CourseRaw
    from app.services.kg.kg_service import import_course

    async with async_session_factory() as session:
        rows = (await session.scalars(
            select(CourseRaw).where(CourseRaw.snapshot["title"].astext == term)
        )).all()
        snapshots = [dict(r.snapshot or {}) for r in rows]

    def _import_all() -> int:
        rebuilt = 0
        with neo4j_driver.session() as neo4j_session:
            for snap in snapshots:
                import_course(neo4j_session, snap)
                rebuilt += 1
        return rebuilt

    return await asyncio.to_thread(_import_all)


@router.post("/llm-decisions/{decision_id}/undo")
async def undo_llm_decision(
    decision_id: str,
    req: LLMDecisionReviewRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("admin:*")),
):
    """撤销一条 auto_applied 治理决策（写 AuditLog；效果反做失败整体 500 不落库）。

    - add_stopword：移除动态停用词条目（归一化链路立即恢复；被连带删除的技能
      节点随后续 JD 入图/聚合自然回填，响应 notes 说明）
    - remove_node / remove_edge（course）：按 course_raw 同名快照重建课程节点与边
    - remove_node(position)：无自动重建路径，明确 409（走重建图恢复）
    - 撤销后 status=reverted，dict_guard 对同实体+同动作跳过自动档
    """
    from datetime import datetime, timedelta, timezone

    from app.api.common import ok, resolve_operator
    from app.core.errors import ERR_CONFLICT, ERR_NOT_FOUND, ERR_VALIDATION
    from app.models.business import AuditLog
    from app.schemas.common import error
    from app.services.llm_decision import (
        DOMAIN_GOVERNANCE,
        STATUS_AUTO_APPLIED,
        STATUS_REVERTED,
    )

    operator, operator_err = resolve_operator(current_user)
    if operator_err is not None:
        return operator_err
    reason = (req.review_reason or "").strip()
    if not reason:
        return error(ERR_VALIDATION, "撤销必须填写 review_reason")

    record = await _load_decision(db, decision_id)
    if record is None:
        return error(ERR_NOT_FOUND, "决策记录不存在", http_status=404)
    if record.domain != DOMAIN_GOVERNANCE or record.status != STATUS_AUTO_APPLIED:
        return error(
            ERR_CONFLICT,
            f"仅 governance 域 auto_applied 记录可撤销（当前 {record.domain}/{record.status}）",
            http_status=409,
        )
    action = (record.structured_output or {}).get("action") or ""
    if action not in _UNDOABLE_ACTIONS:
        return error(ERR_CONFLICT, f"动作 {action!r} 不可撤销", http_status=409)
    if action == "remove_node" and record.entity_type == "position":
        return error(
            ERR_CONFLICT,
            "position 节点删除暂无自动重建路径，请通过图谱重建恢复",
            http_status=409,
        )
    if record.created_at is not None:
        age = datetime.now(timezone.utc) - record.created_at
        if age > timedelta(days=_UNDO_WINDOW_DAYS):
            return error(
                ERR_CONFLICT,
                f"已超过 {_UNDO_WINDOW_DAYS} 天撤销窗口，请走重爬/重建恢复",
                http_status=409,
            )

    term = record.entity_id
    notes: list[str] = []
    filter_removed = False
    rebuilt = 0
    if action == "add_stopword":
        from app.services.extraction import dynamic_filters

        filter_removed = dynamic_filters.remove_entry("blocked", term)
        notes.append("动态停用词已移除；被连带删除的技能节点随后续 JD 入图/聚合自然回填")
    else:
        # remove_node(course) / remove_edge（term 为『技能→课程』，取课程名）
        target = term
        if action == "remove_edge":
            from app.services.extraction.dict_guard import _split_edge

            _, target = _split_edge(term)
        rebuilt = await _rebuild_course_nodes(target)
        if rebuilt == 0:
            notes.append("course_raw 已无同名原始记录，节点待重新采集后由课程入图任务自然恢复")

    record.status = STATUS_REVERTED
    record.reviewer = operator
    record.review_reason = reason
    record.rollback_ref = (
        f"filter_removed:{filter_removed}" if action == "add_stopword" else f"course_rebuilt:{rebuilt}"
    )
    db.add(AuditLog(
        user_id=operator,
        action="llm_decision_undo",
        resource=record.domain,
        resource_id=str(record.id),
        detail={"reason": reason, "action": action, "term": term,
                "filter_removed": filter_removed, "rebuilt": rebuilt},
    ))
    await db.commit()
    return ok({
        "decision_id": str(record.id),
        "filter_removed": filter_removed,
        "rebuilt": rebuilt,
        "notes": notes,
    })
