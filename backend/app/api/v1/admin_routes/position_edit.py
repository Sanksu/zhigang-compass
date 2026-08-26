"""管理后台岗位人工编辑域路由（RBAC admin only）。

对齐契约 /api/v1/admin/positions/{position_name}。技能全量替换 + 文本字段
更新，实际变更写 PositionEditLog 节点（审核员 ID + 时间戳 + diff 摘要）。
Neo4j 同步驱动经 asyncio.to_thread 调用，避免阻塞事件循环。
"""

import asyncio
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends

from app.api.common import resolve_operator
from app.api.deps import require_permission
from app.core.errors import ERR_NOT_FOUND, ERR_VALIDATION
from app.schemas.admin_requests import AdminPositionEditRequest
from app.schemas.common import error, ok
from app.services.kg.id_generator import next_id

router = APIRouter()

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


def _get_position_detail_tx(tx, position_name: str) -> dict | None:
    """读岗位详情（Position 属性 + REQUIRES 技能/学历/证书），岗位不存在返回 None。

    has_edit_log：是否存在该岗位的 PositionEditLog 节点（人工编辑过至少一次），
    供前端展示「已人工校验」标识；count 聚合避免多条日志导致行重复。
    """
    pos = tx.run(
        """
        MATCH (p:Position {name: $name})
        OPTIONAL MATCH (l:PositionEditLog {position_name: $name})
        WITH p, count(l) AS edit_log_count
        RETURN p.id AS id, p.name AS name, p.level AS level, p.industry AS industry,
               p.salary_range AS salary_range, p.status AS status,
               p.core_duties AS core_duties, p.scenarios AS scenarios,
               p.created_at AS created_at, p.updated_at AS updated_at,
               edit_log_count > 0 AS has_edit_log
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
        "has_edit_log": bool(pos["has_edit_log"]),
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
    req: AdminPositionEditRequest,
    current_user: dict = Depends(require_permission("admin:*")),
):
    """人工编辑岗位定义（§12.2），所有实际变更写入 PositionEditLog 节点。

    请求体（均可选，无字段时为空操作返回"无变更"；形状/枚举/权重域由
    Pydantic 强校验，逐项业务校验保留在 validate_position_edit）：
        skills: 技能列表全量替换，每项 {name, necessity: must|nice, weight: 0.0-1.0}
        core_duties / scenarios: 字符串数组，更新 Position 节点属性
    """
    operator, operator_err = resolve_operator(current_user)
    if operator_err is not None:
        return operator_err
    skills = (
        [s.model_dump() for s in req.skills] if req.skills is not None else None
    )
    core_duties = req.core_duties
    scenarios = req.scenarios
    err = validate_position_edit(skills, core_duties, scenarios)
    if err:
        return error(ERR_VALIDATION, err)

    editor_id = operator
    result = await asyncio.to_thread(
        _edit_position_neo4j, position_name, editor_id, skills, core_duties, scenarios
    )
    if not result["exists"]:
        return error(ERR_NOT_FOUND, f"岗位不存在: {position_name}", http_status=404)
    # 编辑已生效：失效图谱热路径缓存（08-18 TTL 治理：panorama/view/search/
    # 节点详情一并失效），避免 300s TTL 窗口内用户读到旧岗位定义
    from app.api.v1.graph import invalidate_graph_caches

    await invalidate_graph_caches()
    return ok(
        data={
            "position_name": position_name,
            "updated": result["updated"],
            "diff_summary": result["diff_summary"],
        },
        msg="无变更" if not result["updated"] else "已保存编辑",
    )
