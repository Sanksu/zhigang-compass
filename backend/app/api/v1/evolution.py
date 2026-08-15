"""演化路由：版本列表、版本 Diff、技能趋势。

数据源：graph_versions 表（PostgreSQL），由图谱版本管理器每日 05:00 写入
T+1 全量快照（设计文档 7.1）。
"""

from collections import Counter
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_role
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
    user: dict = Depends(require_role("guest")),
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
    user: dict = Depends(require_role("guest")),
):
    """两个版本快照 Diff 对比。"""
    va = await db.get(GraphVersion, from_version)
    vb = await db.get(GraphVersion, to_version)
    if va is None or vb is None:
        return error(4040, "版本不存在", http_status=404)
    return ok(data=_diff_snapshots(va.snapshot_json, vb.snapshot_json))


@router.get("/signals")
async def evolution_signals(
    top_n: int = Query(default=10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("guest")),
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
async def version_detail(
    version_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("guest")),
):
    """[M4] 获取版本详情：元信息 + 快照统计 + 节点列表（不含边，避免超载）。

    节点列表 [{id, name, type}]，与 Diff 端点同构，可直接展示。
    """
    v = await db.get(GraphVersion, version_id)
    if v is None:
        return error(4040, "版本不存在", http_status=404)

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


def _build_snapshot_indexes(snapshots: list) -> list[dict]:
    """一次性构建全部快照索引（08-15 超时根因修复）。

    原实现每节点对全部边 sum 统计 freq——O(节点×边)（2 万×6 万×11 期）
    秒级变分钟级，浏览器并发演化请求 30s 超时。索引化后：
      nodes: id → 节点 dict（一次构建复用）
      src/tgt: collections.Counter 边计数（O(边) 一次，节点查 freq O(1)）
    """
    indexes = []
    for v in snapshots:
        snapshot = v.snapshot_json or {}
        nodes = {n.get("id"): n for n in snapshot.get("nodes", []) if isinstance(n, dict)}
        edges = snapshot.get("edges", []) or []
        indexes.append({
            "v": v,
            "nodes": nodes,
            "src": Counter(e.get("source") for e in edges),
            "tgt": Counter(e.get("target") for e in edges),
        })
    return indexes


def _rebuild_node_evolution(
    indexes: list[dict], node_id: str, edge_side: str = "source"
) -> tuple[str, list]:
    """从快照索引重建节点演化轨迹（岗位 source 侧 / 技能 target 侧）。

    返回 (最近名称或 id, points)，points 时间升序
    （date/version/freq=节点被引用边数/present=节点是否存在）。
    """
    name = None
    points = []
    for idx in indexes:
        node = idx["nodes"].get(node_id)
        if node is not None:
            name = node.get("name") or name
        counter = idx["src"] if edge_side == "source" else idx["tgt"]
        v = idx["v"]
        points.append({
            "date": v.created_at.date().isoformat() if v.created_at else None,
            "version": v.id,
            "freq": counter.get(node_id, 0),
            "present": node is not None,
        })
    return name or node_id, points


def _rebuild_position_evolution(
    indexes: list[dict], position_id: str
) -> dict:
    """从快照索引重建单个岗位的演化轨迹（position_evolution 与新列表端点共用）。"""
    name, points = _rebuild_node_evolution(indexes, position_id, edge_side="source")
    return {"position_id": position_id, "position_name": name, "points": points}


async def _load_snapshots(db: AsyncSession) -> list | None:
    """加载全部版本快照（时间升序）；无数据返回 None（调用方 404）。"""
    rows = await db.scalars(
        select(GraphVersion).order_by(GraphVersion.created_at.asc())
    )
    snapshots = [v for v in rows]
    return snapshots or None


def _top_nodes_by_heat(
    indexes: list[dict], node_kind: str, id_prefix: str, edge_side: str, limit: int
) -> list[tuple[str, str]]:
    """按快照出现热度取 Top-N 节点（/positions 与 /skills 共用）。

    热度 = 出现期数降序、最新引用边数降序；返回 [(id, 最近名称)]。
    node_kind: 快照节点 type 值；id_prefix: id 前缀兜底；edge_side:
    "src"/"tgt"——岗位 source 侧 / 技能 target 侧（与 _rebuild_node_evolution 一致）。
    """
    heat: dict[str, dict] = {}  # node_id -> {name, count, latest_freq}
    for idx in indexes:
        for nid, node in idx["nodes"].items():
            if node.get("type") != node_kind and not nid.startswith(id_prefix):
                continue
            rec = heat.setdefault(nid, {"name": None, "count": 0, "latest_freq": 0})
            rec["name"] = node.get("name") or rec["name"]
            rec["count"] += 1
            rec["latest_freq"] = idx[edge_side].get(nid, 0)
    top = sorted(
        heat.items(), key=lambda kv: (-kv[1]["count"], -kv[1]["latest_freq"])
    )[:limit]
    return [(nid, rec["name"]) for nid, rec in top]


@router.get("/position/{id}/evolution")
async def position_evolution(
    id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("guest")),
):
    """[M4] 岗位演化历史：从版本快照序列重建该岗位节点存在性与引用边数变化。

    返回 points（时间升序，date/version/freq=该岗位被引用边数），
    并附当前岗位名（快照中最近出现过的名称）。
    """
    snapshots = await _load_snapshots(db)
    if snapshots is None:
        return error(4040, "无图谱版本数据", http_status=404)
    indexes = _build_snapshot_indexes(snapshots)
    return ok(data=_rebuild_position_evolution(indexes, id))
@router.get("/positions")
async def position_evolution_list(
    limit: int = Query(default=8, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("guest")),
):
    """默认岗位演化列表：按快照出现热度（出现期数、最新引用边数）取 Top-N。

    供演化看板默认展示——页面加载即有岗位演化轨迹，无需先查节点 ID。
    岗位判定：快照节点 type=position（id 前缀 pos_ 兜底）。
    """
    snapshots = await _load_snapshots(db)
    if snapshots is None:
        return error(4040, "无图谱版本数据", http_status=404)

    indexes = _build_snapshot_indexes(snapshots)
    top = _top_nodes_by_heat(indexes, "position", "pos_", "src", limit)
    return ok(data={
        "positions": [_rebuild_position_evolution(indexes, pid) for pid, _ in top],
    })


@router.get("/skills")
async def skill_evolution_list(
    limit: int = Query(default=8, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("guest")),
):
    """默认技能演化列表：按快照出现热度（出现期数、最新引用边数）取 Top-N。

    与 /positions 同模式，供演化看板技能频次趋势默认展示。
    技能 freq = 被引用边数（edges.target == skill，与 /trends 口径一致）。
    技能判定：快照节点 type=skill（id 前缀 sk_ 兜底）。
    """
    snapshots = await _load_snapshots(db)
    if snapshots is None:
        return error(4040, "无图谱版本数据", http_status=404)

    indexes = _build_snapshot_indexes(snapshots)
    top = _top_nodes_by_heat(indexes, "skill", "sk_", "tgt", limit)
    skills = []
    for sid, _ in top:
        name, points = _rebuild_node_evolution(indexes, sid, edge_side="target")
        skills.append({"skill_id": sid, "skill_name": name, "points": points})
    return ok(data={"skills": skills})


@router.get("/trends")
async def skill_trends(
    skill: str,
    window: int = Query(default=90, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("guest")),
):
    """技能频次趋势：从图谱版本快照序列统计该技能关联边数。

    skill 参数为技能节点 ID（sk_xxxx），由图谱 ID 生成器产出。
    """
    # created_at 为 DateTime(timezone=True)，须用带时区的东八区 now 比较，否则偏移 8h
    since = datetime.now(timezone(timedelta(hours=8))) - timedelta(days=window)
    rows = await db.scalars(
        select(GraphVersion)
        .where(GraphVersion.created_at >= since)
        .order_by(GraphVersion.created_at)
    )

    points = []
    for v in rows:
        edges = (v.snapshot_json or {}).get("edges", [])
        freq = sum(
            1 for e in edges if e.get("target") == skill
        )
        points.append({
            "date": v.created_at.date().isoformat() if v.created_at else None,
            "version": v.id,
            "freq": freq,
        })

    return ok(data={"skill": skill, "window": window, "points": points})


@router.get("/state-machine")
async def state_machine_overview(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("guest")),
):
    """[M4] 岗位状态机总览：六态分布 + 最近流转记录（登录用户可读）。

    - states: discovery_candidates 按 state 分组计数（candidate/emerging/
      stable/declining/archived/rejected 六态，含 rejected 终态）
    - transitions: audit_logs 中 action=discovery.state_transition 最近 20 条
      （detail.from_state / to_state / reason，resource_id = 岗位名）

    流转记录仅覆盖人工审核（operator 为 admin 用户名）。自动流转由每日
    discovery_auto_transition 任务直接落 Neo4j Position.status + 候选池
    状态，不写 AuditLog（system 无 users 外键，见 tasks.py 注释）。
    """
    from app.models.business import AuditLog, DiscoveryCandidate

    state_rows = (await db.execute(
        select(DiscoveryCandidate.state, func.count()).group_by(DiscoveryCandidate.state)
    )).all()
    order = ["candidate", "emerging", "stable", "declining", "archived", "rejected"]
    counts = {state: 0 for state in order}
    for state, cnt in state_rows:
        counts[state] = counts.get(state, 0) + cnt

    logs = (await db.scalars(
        select(AuditLog)
        .where(AuditLog.action == "discovery.state_transition")
        .order_by(AuditLog.created_at.desc())
        .limit(20)
    )).all()
    transitions = [
        {
            "id": log.id,
            "position_name": log.resource_id,
            "operator": log.user_id,
            "from_state": (log.detail or {}).get("from_state"),
            "to_state": (log.detail or {}).get("to_state"),
            "reason": (log.detail or {}).get("reason", ""),
            "created_at": log.created_at.isoformat() if log.created_at else None,
        }
        for log in logs
    ]
    return ok(data={"states": counts, "transitions": transitions})


@router.get("/watch")
async def technology_watch_overview(
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=20, ge=1, le=100),
    user: dict = Depends(require_role("guest")),
):
    """观察池公开摘要 + MLI 产业化拐点排名（设计文档 §7.2.5，前端看板）。

    按技能聚合 technology_watch 各源最新信号值，用 MLI（媒介落差指数，四维
    等权）排序输出 Top-N；mli > 0.6 标记 ready_to_industrialize。
    数据来自 watch_signal_daily 每日任务，仅返回公开摘要（无审核队列细节）。
    """
    from app.models.business import TechnologyWatch
    from app.services.discovery.mli import compute_mli

    rows = (await db.scalars(
        select(TechnologyWatch).order_by(TechnologyWatch.last_signal_at.desc())
    )).all()
    # 按技能聚合各源最新信号值
    by_skill: dict[str, dict] = {}
    for r in rows:
        entry = by_skill.setdefault(r.skill_name, {})
        entry.setdefault("sources", {})[r.signal_source] = r.signal_value
        entry["status"] = r.status
        entry["last_signal_at"] = r.last_signal_at

    items = []
    for skill, info in by_skill.items():
        src = info["sources"]
        mli = compute_mli(
            z_paper=src.get("arxiv"),
            z_course=src.get("course"),
            z_community=src.get("github") or src.get("community"),
            growth_jd=src.get("jd"),
        )
        items.append({
            "skill_name": skill,
            "sources": sorted(src),
            "mli": mli.mli,
            "ready_to_industrialize": mli.ready_to_industrialize,
            "status": info["status"],
            "last_signal_at": info["last_signal_at"].isoformat() if info.get("last_signal_at") else None,
        })
    items.sort(key=lambda x: (-x["mli"], x["skill_name"]))
    return ok(data={"items": items[:limit], "total": len(items)})
