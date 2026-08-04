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

    快照结构约定为 {nodes: [{id, name, type}], edges: [{source, target}]}。
    返回的 nodes_* 为 [{id, name, type}]，供前端直接展示节点真实名称。
    """
    a_nodes = {n["id"]: n for n in a.get("nodes", []) if isinstance(n, dict) and n.get("id")}
    b_nodes = {n["id"]: n for n in b.get("nodes", []) if isinstance(n, dict) and n.get("id")}
    a_edges = {f"{e.get('source')}->{e.get('target')}" for e in a.get("edges", [])}
    b_edges = {f"{e.get('source')}->{e.get('target')}" for e in b.get("edges", [])}

    def _ref(node_id: str) -> dict:
        # 名称取新版本优先（节点删除时回退旧版本），无 name 属性时退回 id
        n = b_nodes.get(node_id) or a_nodes.get(node_id)
        return {
            "id": node_id,
            "name": n.get("name") or node_id,
            "type": n.get("type", ""),
        }

    return {
        "nodes_added": sorted(
            (_ref(i) for i in b_nodes.keys() - a_nodes.keys()), key=lambda x: x["id"]
        ),
        "nodes_removed": sorted(
            (_ref(i) for i in a_nodes.keys() - b_nodes.keys()), key=lambda x: x["id"]
        ),
        "nodes_changed": sorted(
            (_ref(i) for i in a_nodes.keys() & b_nodes.keys()), key=lambda x: x["id"]
        ),
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


@router.get("/signals")
async def evolution_signals(
    top_n: int = Query(default=10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    """新兴/衰退技能 Top-N（设计文档 §7.1 Z-score 信号）。

    从 graph_versions 快照序列重建技能频次窗口 → EvolutionDetector 计算
    Z-score → 按 confidence 降序取 emerging（z>2.0）/ declining（z<-1.5）。
    快照不足 2 期（冷启动）时返回空列表，不武断判定。
    """
    rows = await db.scalars(
        select(GraphVersion).order_by(GraphVersion.created_at.asc())
    )
    snapshots = [v.snapshot_json or {} for v in rows]

    from app.services.evolution.trend_service import detect_signals_from_snapshots, rank_signals

    signals = detect_signals_from_snapshots(snapshots)
    emerging = rank_signals(signals, "emerging", top_n)
    declining = rank_signals(signals, "declining", top_n)

    return ok(data={
        "window_count": len(snapshots),
        "emerging": [s.model_dump() for s in emerging],
        "declining": [s.model_dump() for s in declining],
    })


@router.get("/versions/{version_id}")
async def version_detail(version_id: str, db: AsyncSession = Depends(get_db)):
    """[M4] 获取版本详情：元信息 + 快照统计 + 节点列表（不含边，避免超载）。

    节点列表 [{id, name, type}]，与 Diff 端点同构，可直接展示。
    """
    v = await db.get(GraphVersion, version_id)
    if v is None:
        return error(404, "版本不存在")

    snapshot = v.snapshot_json or {}
    nodes = snapshot.get("nodes", [])
    edges = snapshot.get("edges", [])
    by_type: dict[str, int] = {}
    for n in nodes:
        t = n.get("type", "unknown")
        by_type[t] = by_type.get(t, 0) + 1

    return ok(data={
        "version_id": v.id,
        "created_at": v.created_at.isoformat() if v.created_at else None,
        "change_summary": v.change_summary,
        "triggered_by": v.triggered_by,
        "node_added": v.node_added,
        "node_removed": v.node_removed,
        "node_changed": v.node_changed,
        "stats": {"nodes": len(nodes), "edges": len(edges), "by_type": by_type},
        "nodes": [
            {"id": n.get("id"), "name": n.get("name", n.get("id")), "type": n.get("type")}
            for n in nodes if isinstance(n, dict)
        ],
    })


@router.get("/position/{id}/evolution")
async def position_evolution(id: str, db: AsyncSession = Depends(get_db)):
    """[M4] 岗位演化历史：从版本快照序列重建该岗位节点存在性与引用边数变化。

    返回 points（时间升序，date/version/freq=该岗位被引用边数），
    并附当前岗位名（快照中最近出现过的名称）。
    """
    rows = await db.scalars(
        select(GraphVersion).order_by(GraphVersion.created_at.asc())
    )
    snapshots = [v for v in rows]
    if not snapshots:
        return error(404, "无图谱版本数据")

    name = None
    points = []
    for v in snapshots:
        snapshot = v.snapshot_json or {}
        nodes = {n.get("id"): n for n in snapshot.get("nodes", []) if isinstance(n, dict)}
        node = nodes.get(id)
        if node is not None:
            name = node.get("name") or name
        freq = sum(1 for e in snapshot.get("edges", []) if e.get("source") == id)
        points.append({
            "date": v.created_at.date().isoformat() if v.created_at else None,
            "version": v.id,
            "freq": freq,
            "present": node is not None,
        })

    return ok(data={"position_id": id, "position_name": name or id, "points": points})


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
