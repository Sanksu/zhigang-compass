"""新岗位发现路由（08-27）：近期新岗位 + 技能、岗位技能增减对比。

数据源：
- 近期新岗位：PostgreSQL `discovery_candidates`（detected_at 降序近 N 天）+
  Neo4j Position→REQUIRES→Skill（按 position_name 反查 id 取技能）。
- 岗位技能增减：graph_versions.snapshot_json（PostgreSQL 全量快照），取最近
  两期，按岗位 source 过滤 REQUIRES 边做集合差。

权限：get_optional_user（匿名/guest 可读，对齐 /evolution 域 08-28 游客开放）。candidate 态
候选在图内可能为空（未聚合），用 skill_pending 标注「待审核」，不误报真实无技能。
"""

import asyncio
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_optional_user
from app.core.database import get_db, neo4j_driver
from app.core.errors import ERR_NOT_FOUND
from app.models.business import DiscoveryCandidate, GraphVersion
from app.schemas.common import error, ok
from app.services.graph import repository, visibility

router = APIRouter()

# 东八区（快照 created_at 为 timestamptz；近 N 天窗口须带时区比较）
_TZ = timezone(timedelta(hours=8))


def _requires_edges(edges: list) -> list:
    """REQUIRES 边集过滤（岗位→技能口径，与 evolution.py 同约定）。"""
    if any(e.get("relation") for e in edges):
        return [e for e in edges if e.get("relation") == "REQUIRES"]
    return edges


def _resolve_pair(versions, from_version: str | None, to_version: str | None):
    """解析对比版本对 (older, newer)。

    缺省取最近两期（versions 按 created_at 降序）；显式指定时按 id 解析并
    尊重给定方向（from=基准侧，to=目标侧）。单侧指定时另一侧取缺省最新/次新。
    找不到版本 / from==to → ValueError（调用方转 404）。
    """
    default_newer, default_older = versions[0], versions[1]
    if from_version and to_version:
        older_id, newer_id = from_version, to_version
    elif from_version:
        older_id, newer_id = from_version, default_newer.id
    elif to_version:
        older_id, newer_id = default_older.id, to_version
    else:
        return default_older, default_newer

    if older_id == newer_id:
        raise ValueError("from_version 与 to_version 不得相同")
    by_id = {str(v.id): v for v in versions}
    missing = [v for v in (older_id, newer_id) if v not in by_id]
    if missing:
        raise ValueError(f"版本不存在：{'、'.join(missing)}")
    return by_id[older_id], by_id[newer_id]


def _position_skills_at(snapshot: dict) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    """快照 → (岗位→技能 id→name 映射, 全量节点 id→name)。

    按岗位 source（REQUIRES 边 source）分组，供单岗位与全岗位汇总共用。
    """
    node_by_id: dict[str, str] = {}
    for n in snapshot.get("nodes", []):
        if isinstance(n, dict) and n.get("id"):
            node_by_id[str(n["id"])] = n.get("name") or str(n["id"])
    pos_skills: dict[str, dict[str, str]] = {}
    for e in _requires_edges(snapshot.get("edges", [])):
        tgt = str(e.get("target", ""))
        # 仅统计技能（sk_ 前缀，与 trend_service A-1① 同口径）：REQUIRES 的
        # target 还包括 Education（学历要求，如「本科 · 计算机科学」）/Tool/
        # Certification 节点——不过滤会把学历条目当技能混进增减列表（226 实证）。
        if not tgt.startswith("sk_"):
            continue
        src = str(e.get("source", ""))
        pos_skills.setdefault(src, {})[tgt] = node_by_id.get(tgt, tgt)
    return pos_skills, node_by_id


@router.get("/recent")
async def discovery_recent(
    days: int = Query(default=30, ge=1, le=365),
    state: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: Optional[dict] = Depends(get_optional_user),
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
    # candidate 待审核岗位不外泄：对齐图谱域单一事实源（services/graph/
    # visibility.py）——匿名与 guest 均走 public scope（第七轮审查 P1-4，
    # 原实现只防匿名，登录 guest 可见待审核岗位名与定义草稿）
    if not visibility._can_view_all_positions(user):
        rows = [r for r in rows if r.state != "candidate"]
    total = len(rows)
    rows = rows[:limit]

    # 批量回查图谱技能 + 定义属性：按 position_name → id → must/nice/soft、core_duties/scenarios
    skills_by_name: dict[str, dict] = {}
    props_by_name: dict[str, dict] = {}
    id_by_name: dict[str, str] = {}
    # Neo4j 查询为同步 CPU/IO，放线程池
    def _load_skills():
        for r in rows:
            pid, skills = repository.query_position_skills_by_name(
                neo4j_driver, r.position_name
            )
            id_by_name[r.position_name] = pid
            skills_by_name[r.position_name] = skills
            props_by_name[r.position_name] = repository.query_position_definition_by_name(
                neo4j_driver, r.position_name
            )

    await asyncio.to_thread(_load_skills)

    def _compose_definition(r) -> dict:
        """组装赛题五字段结构化定义。

        职责/场景：岗位已落图时以图谱属性为准（JD 聚合 + 人工优化结果），
        candidate 未落图时回退 RAG 阶段二 LLM 结构化草案；
        必备/加分技能一律取图谱 REQUIRES 证据边，不采信 LLM 生成（第三道防线）。
        """
        st = r.definition_structured or {}
        props = props_by_name.get(r.position_name) or {}
        skills = skills_by_name.get(r.position_name) or {}
        return {
            "position_name": r.position_name,
            "summary": r.definition_draft or "",
            "core_duties": list(props.get("core_duties") or st.get("core_duties") or []),
            "must_skills": [
                s["skill_name"] for s in skills.get("must", []) if s.get("skill_name")
            ],
            "nice_skills": [
                s["skill_name"] for s in skills.get("nice", []) if s.get("skill_name")
            ],
            "typical_scenarios": list(
                props.get("scenarios") or st.get("typical_scenarios") or []
            ),
        }

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
            "definition": _compose_definition(r),
            "confidence": r.confidence,
            "skills": skills if has_skills else None,
            # 图内无该岗位技能（candidate 未聚合/无 JD 证据）→ 标注待审核，不误报真实无技能
            "skill_pending": not has_skills,
        })
    return ok(data={"candidates": candidates, "total": total})


@router.get("/position-skills-delta/summary")
async def position_skills_delta_summary(
    from_version: str | None = Query(default=None, description="对比基准版 id（缺省=次新版本）"),
    to_version: str | None = Query(default=None, description="目标版 id（缺省=最新版本）"),
    db: AsyncSession = Depends(get_db),
    user: Optional[dict] = Depends(get_optional_user),
):
    """全岗位技能增减汇总（两版快照对比）。

    一次返回全部岗位的技能增减计数 + 可用版本列表：下拉只列有增减岗位、
    稳定面板列无增减岗位，均由本端点驱动，避免逐岗位请求。
    """
    # ⚠️ 多列 select 必须 execute().all()——scalars() 会降维成第一列标量
    # （2026-08-28 226 实证：versions 变 id 字符串列表，older.id 直接 500）
    versions = (await db.execute(
        select(GraphVersion.id, GraphVersion.created_at)
        .order_by(GraphVersion.created_at.desc())
    )).all()
    if len(versions) < 2:
        return error(ERR_NOT_FOUND, "无足够图谱版本数据（快照不足 2 期）", http_status=404)

    try:
        older, newer = _resolve_pair(versions, from_version, to_version)
    except ValueError as e:
        return error(ERR_NOT_FOUND, str(e), http_status=404)

    pair_rows = list(await db.scalars(
        select(GraphVersion).where(GraphVersion.id.in_([older.id, newer.id]))
    ))
    snap_by_id = {r.id: (r.snapshot_json or {}) for r in pair_rows}
    old_pos, old_names = _position_skills_at(snap_by_id[older.id])
    new_pos, new_names = _position_skills_at(snap_by_id[newer.id])

    items = []
    for src in sorted(set(old_pos) | set(new_pos)):
        old_ids = set(old_pos.get(src, {}))
        new_ids = set(new_pos.get(src, {}))
        items.append({
            "position_id": src,
            # 岗位名优先取目标版节点名（可能已改名），其次基准版，最后回退 id
            "position_name": new_names.get(src) or old_names.get(src) or src,
            "added": len(new_ids - old_ids),
            "removed": len(old_ids - new_ids),
            "unchanged": len(new_ids & old_ids),
        })
    # 有增减的岗位排前（下拉选项即该前缀），同幅度按名称
    items.sort(key=lambda x: (-(x["added"] + x["removed"]), x["position_name"]))

    return ok(data={
        "from_version": older.id,
        "from_created_at": older.created_at.isoformat() if older.created_at else None,
        "to_version": newer.id,
        "to_created_at": newer.created_at.isoformat() if newer.created_at else None,
        "versions": [
            {"id": str(v.id), "created_at": v.created_at.isoformat() if v.created_at else None}
            for v in versions
        ],
        "positions": items,
    })


@router.get("/position-skills-delta")
async def position_skills_delta(
    position: str = Query(..., description="岗位 id（pos_xxx）或岗位名"),
    from_version: str | None = Query(default=None, description="对比基准版 id（缺省=次新版本）"),
    to_version: str | None = Query(default=None, description="目标版 id（缺省=最新版本）"),
    db: AsyncSession = Depends(get_db),
    user: Optional[dict] = Depends(get_optional_user),
):
    """岗位技能增减（两版快照对比，缺省最近两期，可显式指定 from/to 版本）。
    """
    # ⚠️ 多列 select 必须 execute().all()——scalars() 会降维成第一列标量
    # （2026-08-28 226 实证：versions 变 id 字符串列表，older.id 直接 500）
    versions = (await db.execute(
        select(GraphVersion.id, GraphVersion.created_at)
        .order_by(GraphVersion.created_at.desc())
    )).all()
    if len(versions) < 2:
        return error(ERR_NOT_FOUND, "无足够图谱版本数据（快照不足 2 期）", http_status=404)

    try:
        older, newer = _resolve_pair(versions, from_version, to_version)
    except ValueError as e:
        return error(ERR_NOT_FOUND, str(e), http_status=404)

    pair_rows = list(await db.scalars(
        select(GraphVersion).where(GraphVersion.id.in_([older.id, newer.id]))
    ))
    snap_by_id = {r.id: (r.snapshot_json or {}) for r in pair_rows}

    def _skills_at(snapshot: dict) -> dict[str, str]:
        """快照中该岗位 REQUIRES 的技能 id→name 映射（source 匹配 id 或 name）。"""
        pos_skills, _ = _position_skills_at(snapshot)
        return pos_skills.get(position, {})

    old_skills = _skills_at(snap_by_id[older.id])
    new_skills = _skills_at(snap_by_id[newer.id])

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
