"""管理后台路由：用户管理 / 审计日志 / 爬虫状态 / 岗位审核（RBAC admin only）。

对齐契约 /api/v1/admin/*。岗位审核（positions/pending）读取 DiscoveryCandidate 表
（默认过滤 state=candidate），review 走状态机校验 + 图谱 status 同步 + 审计日志。
"""

import asyncio
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.common import iso, paged_ok, paginate
from app.api.deps import require_permission
from app.api.v1.admin_routes import accounts, audit, crawl, position_reviews
from app.core.database import get_db, redis_client
from app.core.errors import ERR_CONFLICT, ERR_INTERNAL, ERR_NOT_FOUND, ERR_VALIDATION
from app.schemas.common import error, ok
from app.services.kg.id_generator import next_id

router = APIRouter(prefix="/admin", dependencies=[Depends(require_permission("admin:*"))])
router.include_router(accounts.router)
router.include_router(audit.router)
router.include_router(crawl.router)
router.include_router(position_reviews.router)

# 爬虫域私有符号 re-export（tests/admin/* 直连导入）
PLATFORM_META = crawl.PLATFORM_META
_PLATFORM_TO_SPIDER = crawl._PLATFORM_TO_SPIDER
_history_row = crawl._history_row
_match_platform = crawl._match_platform
_crawl_log_events = crawl._crawl_log_events

# 岗位审核域私有符号 re-export（tests/admin/test_positions_pending、tests/matching 直连导入）
positions_pending = position_reviews.positions_pending
_persist_rejected_change = position_reviews._persist_rejected_change
_persist_position_state = position_reviews._persist_position_state


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
