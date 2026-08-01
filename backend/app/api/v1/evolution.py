"""演化路由：版本列表、版本 Diff、技能趋势。

数据源：graph_versions 表（PostgreSQL），由图谱版本管理器每日 05:00 写入
T+1 全量快照（设计文档 7.1）。
"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.business import GraphVersion
from app.schemas.common import ok, error

router = APIRouter()


def _diff_snapshots(a: dict, b: dict) -> dict:
    """对比两个版本快照（set 差集，设计文档 7.1 Diff）。

    快照结构约定为 {nodes: [{id, ...}], edges: [{source, target, ...}]}。
    """
    a_nodes = {n["id"] for n in a.get("nodes", []) if isinstance(n, dict)}
    b_nodes = {n["id"] for n in b.get("nodes", []) if isinstance(n, dict)}
    a_edges = {f"{e.get('source')}->{e.get('target')}" for e in a.get("edges", [])}
    b_edges = {f"{e.get('source')}->{e.get('target')}" for e in b.get("edges", [])}

    return {
        "nodes_added": sorted(b_nodes - a_nodes),
        "nodes_removed": sorted(a_nodes - b_nodes),
        "nodes_changed": sorted(a_nodes & b_nodes),
        "edges_added": sorted(b_edges - a_edges),
        "edges_removed": sorted(a_edges - b_edges),
    }


@router.get("/versions")
async def list_versions(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=30, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """图谱版本列表（分页，按创建时间倒序）。"""
    total = await db.scalar(select(func.count()).select_from(GraphVersion))
    rows = await db.scalars(
        select(GraphVersion)
        .order_by(GraphVersion.created_at.desc())
        .offset((page - 1) * size)
        .limit(size)
    )
    items = [
        {
            "version_id": v.id,
            "created_at": v.created_at.isoformat() if v.created_at else None,
            "change_summary": v.change_summary,
            "triggered_by": v.triggered_by,
            "node_added": v.node_added,
            "node_removed": v.node_removed,
            "node_changed": v.node_changed,
        }
        for v in rows
    ]
    return ok(data={"items": items, "total": total or 0, "page": page, "size": size})


@router.get("/diff")
async def version_diff(
    from_version: str = Query(..., alias="from"),
    to_version: str = Query(..., alias="to"),
    db: AsyncSession = Depends(get_db),
):
    """两个版本快照 Diff 对比。"""
    va = await db.get(GraphVersion, from_version)
    vb = await db.get(GraphVersion, to_version)
    if va is None or vb is None:
        return error(404, "版本不存在")
    return ok(data=_diff_snapshots(va.snapshot_json, vb.snapshot_json))


@router.get("/trends")
async def skill_trends(
    skill: str,
    window: int = Query(default=90, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
):
    """技能频次趋势：从图谱版本快照序列统计该技能关联边数。

    skill 参数为技能节点 ID（sk_xxxx），由图谱 ID 生成器产出。
    """
    since = datetime.utcnow() - timedelta(days=window)
    rows = await db.scalars(
        select(GraphVersion)
        .where(GraphVersion.created_at >= since)
        .order_by(GraphVersion.created_at)
    )

    points = []
    for v in rows:
        edges = v.snapshot_json.get("edges", [])
        freq = sum(
            1 for e in edges if e.get("target") == skill
        )
        points.append({
            "date": v.created_at.date().isoformat() if v.created_at else None,
            "version": v.id,
            "freq": freq,
        })

    return ok(data={"skill": skill, "window": window, "points": points})
