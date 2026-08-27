"""新岗位发现路由（08-27）：近期新岗位 + 技能、岗位技能增减对比。

数据源：
- 近期新岗位：PostgreSQL `discovery_candidates`（detected_at 降序近 N 天）+
  Neo4j Position→REQUIRES→Skill（按 position_name 反查 id 取技能）。
- 岗位技能增减：graph_versions.snapshot_json（PostgreSQL 全量快照），取最近
  两期，按岗位 source 过滤 REQUIRES 边做集合差。

权限：require_role("guest")（登录可见，对齐 /evolution 域）。candidate 态
候选在图内可能为空（未聚合），用 skill_pending 标注「待审核」，不误报真实无技能。
"""

import asyncio
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_role
from app.core.database import get_db, neo4j_driver
from app.core.errors import ERR_NOT_FOUND
from app.models.business import DiscoveryCandidate, GraphVersion
from app.schemas.common import error, ok
from app.services.graph import repository

router = APIRouter()

# 东八区（快照 created_at 为 timestamptz；近 N 天窗口须带时区比较）
_TZ = timezone(timedelta(hours=8))


def _requires_edges(edges: list) -> list:
    """REQUIRES 边集过滤（岗位→技能口径，与 evolution.py 同约定）。"""
    if any(e.get("relation") for e in edges):
        return [e for e in edges if e.get("relation") == "REQUIRES"]
    return edges


@router.get("/recent")
async def discovery_recent(
    days: int = Query(default=30, ge=1, le=365),
    state: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("guest")),
):
    """近期发现的新岗位及其技能（detected_at 降序近 N 天）。"""
    since = datetime.now(_TZ) - timedelta(days=days)
    stmt = (
        select(DiscoveryCandidate)
        .where(DiscoveryCandidate.detected_at >= since.isoformat())
        .order_by(DiscoveryCandidate.detected_at.desc())
    )
    if state:
        stmt = stmt.where(DiscoveryCandidate.state == state)
    rows = list(await db.scalars(stmt))
    total = len(rows)
    rows = rows[:limit]

    # 批量回查图谱技能：按 position_name → id → must/nice/soft
    skills_by_name: dict[str, dict] = {}
    id_by_name: dict[str, str] = {}
    # Neo4j 查询为同步 CPU/IO，放线程池
    def _load_skills():
        for r in rows:
            pid, skills = repository.query_position_skills_by_name(
                neo4j_driver, r.position_name
            )
            id_by_name[r.position_name] = pid
            skills_by_name[r.position_name] = skills

    await asyncio.to_thread(_load_skills)

    candidates = []
    for r in rows:
        skills = skills_by_name.get(r.position_name)
        has_skills = bool(skills and (skills.get("must") or skills.get("nice") or skills.get("soft")))
        candidates.append({
            "position_id": id_by_name.get(r.position_name),
            "position_name": r.position_name,
            "state": r.state,
            "detected_at": r.detected_at,
            "definition_draft": r.definition_draft or "",
            "confidence": r.confidence,
            "skills": skills if has_skills else None,
            # 图内无该岗位技能（candidate 未聚合/无 JD 证据）→ 标注待审核，不误报真实无技能
            "skill_pending": not has_skills,
        })
    return ok(data={"candidates": candidates, "total": total})


@router.get("/position-skills-delta")
async def position_skills_delta(
    position: str = Query(..., description="岗位 id（pos_xxx）或岗位名"),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("guest")),
):
    """岗位技能增减（最近两个版本快照对比，按岗位 source 过滤 REQUIRES 边）。
    """
    rows = list(await db.scalars(
        select(GraphVersion).order_by(GraphVersion.created_at.desc()).limit(2)
    ))
    if len(rows) < 2:
        return error(ERR_NOT_FOUND, "无足够图谱版本数据（快照不足 2 期）", http_status=404)

    newer, older = rows[0], rows[1]

    def _skills_at(snapshot: dict) -> dict[str, str]:
        """快照中该岗位 REQUIRES 的技能 id→name 映射（source 匹配 id 或 name）。"""
        node_by_id: dict[str, str] = {}
        for n in snapshot.get("nodes", []):
            if isinstance(n, dict) and n.get("id"):
                node_by_id[str(n["id"])] = n.get("name") or str(n["id"])
        out: dict[str, str] = {}
        for e in _requires_edges(snapshot.get("edges", [])):
            src = str(e.get("source", ""))
            tgt = str(e.get("target", ""))
            # 岗位 source：id 或 name 双匹配
            if src != position:
                continue
            out[tgt] = node_by_id.get(tgt, tgt)
        return out

    old_skills = _skills_at(older.snapshot_json or {})
    new_skills = _skills_at(newer.snapshot_json or {})

    old_ids = set(old_skills)
    new_ids = set(new_skills)
    added = [{"skill_id": sid, "skill_name": new_skills[sid]} for sid in sorted(new_ids - old_ids)]
    removed = [{"skill_id": sid, "skill_name": old_skills[sid]} for sid in sorted(old_ids - new_ids)]
    unchanged = [{"skill_id": sid, "skill_name": new_skills[sid]} for sid in sorted(new_ids & old_ids)]

    return ok(data={
        "position_id": position,
        "position_name": position,
        "from_version": older.id,
        "from_created_at": older.created_at.isoformat() if older.created_at else None,
        "to_version": newer.id,
        "to_created_at": newer.created_at.isoformat() if newer.created_at else None,
        "added": added,
        "removed": removed,
        "unchanged": unchanged,
    })
