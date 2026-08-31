"""演化路由：版本列表、版本 Diff、技能趋势。

数据源：graph_versions 表（PostgreSQL），由图谱版本管理器每日 05:00 写入
T+1 全量快照（设计文档 7.1）。
"""

import asyncio
import json
from collections import Counter
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from typing import Optional
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.common import iso, paged_ok, paginate
from app.core import runtime_config
from app.api.deps import get_optional_user
from app.core.database import get_db, redis_client
from app.core.errors import ERR_NOT_FOUND
from app.models.business import EvolutionEvent, GraphVersion
from app.services.graph import visibility
from app.schemas.common import error, ok

router = APIRouter()

# 演化列表缓存 TTL（默认 60s）：快照每日 05:00 更新，列表查询每请求全量加载
# 快照建索引（O(快照×节点×边)），看板高频访问下重复计算（08-15 审查）。
# 缓存是增强非正确性依赖：redis 不可用（测试环境/故障）时自动降级直查。
# 08-16 管理后台可编辑（runtime_settings.json，重启生效）
EVOLUTION_CACHE_TTL = runtime_config.get("evolution_cache_ttl", 60)


async def _cache_get_json(key: str):
    """Redis 缓存读取（JSON）；redis 不可用时返回 None（不阻塞主查询）。"""
    try:
        cached = await redis_client.get(key)
        return json.loads(cached) if cached else None
    except Exception:
        return None


async def _cache_set_json(key: str, data, ttl: int = EVOLUTION_CACHE_TTL) -> None:
    """Redis 缓存写入；失败静默（缓存写失败不影响响应正确性）。"""
    try:
        await redis_client.set(key, json.dumps(data), ex=ttl)
    except Exception:
        pass


# 快照全量加载单飞（沿袭 graph.py in-flight 表）：演化看板一次并发拉起
# 4-5 个端点，各自全量加载快照建索引（每期 ~20k 节点+60k 边）会复制同量级
# 副本并叠加 PG 传输——同进程并发 miss 合流，跟随者 await 首个加载结果
# （08-15 演化看板 30s 超时/TTL 风暴教训在新模块的回归，第五轮审查 P1-3）
_snapshots_inflight: dict[str, asyncio.Future] = {}


async def _load_snapshots(db: AsyncSession) -> list | None:
    """加载全部版本快照（时间升序）；无数据返回 None（调用方 404）。"""
    inflight = _snapshots_inflight.get("all")
    if inflight is not None:
        return await inflight

    future = asyncio.get_running_loop().create_future()
    _snapshots_inflight["all"] = future
    try:
        rows = list(await db.scalars(
            select(GraphVersion).order_by(GraphVersion.created_at.asc())
        ))
        snapshots = rows or None
        future.set_result(snapshots)
        return snapshots
    except BaseException as exc:
        # BaseException（第八轮 P2-6，与 graph.py single-flight 同口径）：
        # 请求方取消时 CancelledError 不走 Exception 分支——leader 挂掉则
        # future 永不 resolve，跟随者 await 挂死。注入异常后原样 raise。
        if not future.done():
            future.set_exception(exc)
        raise
    finally:
        _snapshots_inflight.pop("all", None)


def _requires_edges(edges: list) -> list:
    """REQUIRES 边集过滤（岗位→技能口径统一，与 trend_service A-1① 同约定）。

    BELONGS_TO/ALTERNATIVE_OF/PREREQUISITE_OF/EVOLVED_FROM 等技能→技能边的
    target 同为 sk_ 前缀，不过滤会混入「岗位」列/频次统计；旧快照边无
    relation 标注则整体放行（历史口径兼容）。
    """
    if any(e.get("relation") for e in edges):
        return [e for e in edges if e.get("relation") == "REQUIRES"]
    return edges


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
    user: Optional[dict] = Depends(get_optional_user),
):
    """图谱版本列表（分页，按创建时间倒序）。"""
    stmt = select(GraphVersion).order_by(GraphVersion.created_at.desc())
    rows, total = await paginate(
        db, stmt, page, size, count_stmt=select(func.count()).select_from(GraphVersion)
    )
    items = [
        {
            "version_id": v.id,
            "created_at": iso(v.created_at),
            "change_summary": v.change_summary,
            "triggered_by": v.triggered_by,
            "node_added": v.node_added,
            "node_removed": v.node_removed,
            "node_changed": v.node_changed,
            "data_warning": v.data_warning,
        }
        for v in rows
    ]
    return paged_ok(items, total, page, size)


@router.get("/diff")
async def version_diff(
    from_version: str = Query(..., alias="from"),
    to_version: str = Query(..., alias="to"),
    db: AsyncSession = Depends(get_db),
    user: Optional[dict] = Depends(get_optional_user),
):
    """两个版本快照 Diff 对比。"""
    va = await db.get(GraphVersion, from_version)
    vb = await db.get(GraphVersion, to_version)
    if va is None or vb is None:
        return error(ERR_NOT_FOUND, "版本不存在", http_status=404)
    return ok(data=_diff_snapshots(va.snapshot_json, vb.snapshot_json))


def _annotate_anti_fluctuation(
    rows: list,
    snapshots: list[dict],
    signals: list,
) -> None:
    """抗波动补强：给信号打 warning 标 + 附 freq_ratio 展示口径（打标不剔除）。

    - warning：解读期（最近两期快照）任一命中 data_warning → 所有信号打标
      （z 的 current 与紧邻历史窗口都落在解读期内，证据量异常时读数不可靠）；
    - freq_ratio：当期频次 / 当期 REQUIRES 总边数（分母 0 → None）。
    """
    interpretation_warning = any(v.data_warning for v in rows[-2:])
    requires_total = sum(
        1 for e in snapshots[-1].get("edges", []) if e.get("relation") == "REQUIRES"
    ) if snapshots else 0
    for s in signals:
        s.warning = interpretation_warning
        s.freq_ratio = round(s.current_freq / requires_total, 4) if requires_total else None


@router.get("/signals")
async def evolution_signals(
    top_n: int = Query(default=10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    user: Optional[dict] = Depends(get_optional_user),
):
    """新兴/衰退技能 Top-N（设计文档 §7.1 Z-score 信号）。

    从 graph_versions 快照序列重建技能频次窗口 → EvolutionDetector 计算
    Z-score → 按 confidence 降序取 emerging（z>2.0）/ declining（z<-1.5）。
    快照不足 2 期（冷启动）时返回空列表，不武断判定。

    双层抗波动：检测侧命中 data_warning 的快照整期剔除（不作为判定输入，
    防总量骤变反向伪信号）；展示侧打标不剔除——解读期（最近两期快照）
    任一命中时 warnings 透出全序列告警明细，信号照常输出仅打 warning 标。
    """
    # 全量快照重建窗口 + 逐技能 Z-score 是看板最重路径，此前无缓存每次
    # 轮询都全量重算（第五轮审查 P1-3）；快照每日一更，TTL 缓存即可
    cache_key = f"evolution:signals:{top_n}"
    cached = await _cache_get_json(cache_key)
    if cached is not None:
        return ok(data=cached)

    rows = list(await db.scalars(
        select(GraphVersion).order_by(GraphVersion.created_at.asc())
    ))
    snapshots = [v.snapshot_json or {} for v in rows]

    from app.services.evolution.trend_service import detect_signals_from_snapshots, rank_signals

    # 检测侧抑制：命中 data_warning 的快照整期剔除（部分源故障的总量骤变
    # 会反向放大占比产生伪 emerging，不作为判定输入）；展示侧仍打标不剔除
    signals = detect_signals_from_snapshots(
        snapshots, degraded_flags=[bool(v.data_warning) for v in rows],
    )
    emerging = rank_signals(signals, "emerging", top_n)
    declining = rank_signals(signals, "declining", top_n)
    _annotate_anti_fluctuation(rows, snapshots, [*emerging, *declining])

    warnings = [
        {
            "version_id": v.id,
            "created_at": iso(v.created_at),
            "warning": v.data_warning,
        }
        for v in reversed(rows)
        if v.data_warning
    ]

    payload = {
        "window_count": len(snapshots),
        "emerging": [s.model_dump() for s in emerging],
        "declining": [s.model_dump() for s in declining],
        "warnings": warnings,
    }
    await _cache_set_json(cache_key, payload)
    return ok(data=payload)


@router.get("/versions/{version_id}")
async def version_detail(
    version_id: str,
    db: AsyncSession = Depends(get_db),
    user: Optional[dict] = Depends(get_optional_user),
):
    """[M4] 获取版本详情：元信息 + 快照统计 + 节点列表（不含边，避免超载）。

    节点列表 [{id, name, type}]，与 Diff 端点同构，可直接展示。
    """
    v = await db.get(GraphVersion, version_id)
    if v is None:
        return error(ERR_NOT_FOUND, "版本不存在", http_status=404)

    snapshot = v.snapshot_json or {}
    nodes = snapshot.get("nodes", [])
    edges = snapshot.get("edges", [])
    by_type: dict[str, int] = {}
    for n in nodes:
        t = n.get("type", "unknown")
        by_type[t] = by_type.get(t, 0) + 1

    return ok(data={
        "version_id": v.id,
        "created_at": iso(v.created_at),
        "change_summary": v.change_summary,
        "triggered_by": v.triggered_by,
        "node_added": v.node_added,
        "node_removed": v.node_removed,
        "node_changed": v.node_changed,
        "data_warning": v.data_warning,
        "stats": {"nodes": len(nodes), "edges": len(edges), "by_type": by_type},
        "nodes": [
            {"id": n.get("id"), "name": n.get("name", n.get("id")), "type": n.get("type")}
            for n in nodes if isinstance(n, dict)
        ],
    })


@router.get("/events")
async def evolution_events(
    version_id: str | None = Query(default=None, description="按版本过滤；缺省返回全部（最新在前）"),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: Optional[dict] = Depends(get_optional_user),
):
    """谱系事件流（机制补强② born/merged/ended 落库数据）。

    由 derive_evolved_from 在快照演化时写入 evolution_events；本端点只读。
    """
    stmt = select(EvolutionEvent).order_by(EvolutionEvent.id.desc()).limit(limit)
    if version_id:
        stmt = (
            select(EvolutionEvent)
            .where(EvolutionEvent.version_id == version_id)
            .order_by(EvolutionEvent.id.desc())
            .limit(limit)
        )
    rows = (await db.scalars(stmt)).all()
    items = [
        {
            "id": e.id,
            "version_id": e.version_id,
            "event_type": e.event_type,
            "from_name": e.from_name,
            "to_name": e.to_name,
            "created_at": iso(e.created_at),
            "detail": e.detail,
        }
        for e in rows
    ]
    return ok(data={"items": items})


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


def _top_nodes_by_heat(
    indexes: list[dict], node_kind: str, id_prefix: str, edge_side: str
) -> list[tuple[str, str]]:
    """按快照出现热度全量排序（/positions 与 /skills 共用，分页切片由调用方做）。

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
    ranked = sorted(
        heat.items(), key=lambda kv: (-kv[1]["count"], -kv[1]["latest_freq"])
    )
    return [(nid, rec["name"]) for nid, rec in ranked]


def _slice_page(
    ranked: list, page: int, size: int
) -> list:
    """分页切片（page 从 1 起，越界返回空列表）。"""
    start = (page - 1) * size
    return ranked[start : start + size]


@router.get("/position/{id}/evolution")
async def position_evolution(
    id: str,
    db: AsyncSession = Depends(get_db),
    user: Optional[dict] = Depends(get_optional_user),
):
    """[M4] 岗位演化历史：从版本快照序列重建该岗位节点存在性与引用边数变化。

    返回 points（时间升序，date/version/freq=该岗位被引用边数），
    并附当前岗位名（快照中最近出现过的名称）。
    """
    cache_key = f"evolution:position:{id}"
    cached = await _cache_get_json(cache_key)
    if cached is not None:
        return ok(data=cached)

    snapshots = await _load_snapshots(db)
    if snapshots is None:
        return error(ERR_NOT_FOUND, "无图谱版本数据", http_status=404)
    indexes = _build_snapshot_indexes(snapshots)
    data = _rebuild_position_evolution(indexes, id)
    await _cache_set_json(cache_key, data)
    return ok(data=data)

@router.get("/positions")
async def position_evolution_list(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=10, ge=1, le=50),
    q: str | None = Query(
        default=None, max_length=100,
        description="按岗位名称模糊过滤（08-16：下拉全量可搜索）"),
    db: AsyncSession = Depends(get_db),
    user: Optional[dict] = Depends(get_optional_user),
):
    """岗位演化历史列表：按快照出现热度（出现期数、最新引用边数）降序分页。

    供演化看板默认展示——页面加载即有岗位演化轨迹，无需先查节点 ID。
    岗位判定：快照节点 type=position（id 前缀 pos_ 兜底）。
    08-16：limit 改 page/size 分页（演化看板翻页，10 项一页），响应含 total；
    q 参数按名称模糊过滤（大小写不敏感），供下拉搜索。
    """
    cache_key = f"evolution:positions:{page}:{size}:{q or ''}"
    cached = await _cache_get_json(cache_key)
    if cached is not None:
        return ok(data=cached)

    snapshots = await _load_snapshots(db)
    if snapshots is None:
        return error(ERR_NOT_FOUND, "无图谱版本数据", http_status=404)

    indexes = _build_snapshot_indexes(snapshots)
    ranked = _top_nodes_by_heat(indexes, "position", "pos_", "src")
    # isinstance 防御：测试直调端点时 q 为 Query 对象（HTTP 下恒为 str）
    if isinstance(q, str) and q.strip():
        ql = q.strip().lower()
        ranked = [(nid, name) for nid, name in ranked if name and ql in name.lower()]
    total = len(ranked)
    top = _slice_page(ranked, page, size)
    data = {
        "positions": [_rebuild_position_evolution(indexes, pid) for pid, _ in top],
        "total": total,
        "page": page,
        "size": size,
    }
    await _cache_set_json(cache_key, data)
    return ok(data=data)


@router.get("/skills")
async def skill_evolution_list(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=10, ge=1, le=50),
    q: str | None = Query(
        default=None, max_length=100,
        description="按技能名称模糊过滤（08-16：下拉全量可搜索）"),
    db: AsyncSession = Depends(get_db),
    user: Optional[dict] = Depends(get_optional_user),
):
    """技能频次趋势列表：按快照出现热度（出现期数、最新引用边数）降序分页。

    与 /positions 同模式，供演化看板技能频次趋势默认展示。
    技能 freq = 被引用边数（edges.target == skill，与 /trends 口径一致）。
    技能判定：快照节点 type=skill（id 前缀 sk_ 兜底）。
    08-16：limit 改 page/size 分页（演化看板翻页，10 项一页），响应含 total；
    q 参数按名称模糊过滤（大小写不敏感），供下拉搜索。
    """
    cache_key = f"evolution:skills:{page}:{size}:{q or ''}"
    cached = await _cache_get_json(cache_key)
    if cached is not None:
        return ok(data=cached)

    snapshots = await _load_snapshots(db)
    if snapshots is None:
        return error(ERR_NOT_FOUND, "无图谱版本数据", http_status=404)

    indexes = _build_snapshot_indexes(snapshots)
    ranked = _top_nodes_by_heat(indexes, "skill", "sk_", "tgt")
    # isinstance 防御：测试直调端点时 q 为 Query 对象（HTTP 下恒为 str）
    if isinstance(q, str) and q.strip():
        ql = q.strip().lower()
        ranked = [(nid, name) for nid, name in ranked if name and ql in name.lower()]
    total = len(ranked)
    top = _slice_page(ranked, page, size)
    skills = []
    for sid, _ in top:
        name, points = _rebuild_node_evolution(indexes, sid, edge_side="target")
        skills.append({"skill_id": sid, "skill_name": name, "points": points})
    data = {"skills": skills, "total": total, "page": page, "size": size}
    await _cache_set_json(cache_key, data)
    return ok(data=data)


@router.get("/trends")
async def skill_trends(
    skill: str,
    window: int = Query(default=90, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    user: Optional[dict] = Depends(get_optional_user),
):
    """技能频次趋势：从图谱版本快照序列统计该技能关联 REQUIRES 边数。

    skill 参数为技能节点 ID（sk_xxxx），由图谱 ID 生成器产出。
    """
    cache_key = f"evolution:trends:{skill}:{window}"
    cached = await _cache_get_json(cache_key)
    if cached is not None:
        return ok(data=cached)

    # created_at 为 DateTime(timezone=True)，须用带时区的东八区 now 比较，否则偏移 8h
    since = datetime.now(timezone(timedelta(hours=8))) - timedelta(days=window)
    rows = await db.scalars(
        select(GraphVersion)
        .where(GraphVersion.created_at >= since)
        .order_by(GraphVersion.created_at)
    )

    points = []
    for v in rows:
        # 仅 REQUIRES 边（P1-2 同根）：趋势曲线喂给桑基时间轴内嵌图，口径须一致
        edges = _requires_edges((v.snapshot_json or {}).get("edges", []))
        freq = sum(
            1 for e in edges if e.get("target") == skill
        )
        points.append({
            "date": v.created_at.date().isoformat() if v.created_at else None,
            "version": v.id,
            "freq": freq,
        })

    data = {"skill": skill, "window": window, "points": points}
    await _cache_set_json(cache_key, data)
    return ok(data=data)


def _build_skill_flow(
    snapshots: list, skill_id: str, top_n: int
) -> dict:
    """从快照序列构建技能关联岗位动态变迁桑基图数据。

    逐期统计要求该技能的岗位，权重=岗位当期 REQUIRES 出度（当期要求的技能
    总数）——真实快照边按 (岗位, 技能) 去重后逐岗位恒为 1，出度是快照内
    唯一有区分度的确定性权重；按 (-出度, 岗位名) 取 Top-N 入列，连线值=
    左侧期次该岗位出度。相邻期次同名岗位连线——纵向看单岗位持续需求厚度，
    横向看关联岗位的进出（无连线的期次即新进入/已离开 Top 榜）。
    该技能无关联岗位的期次整期剔除（早期稀疏快照不产生空列），period_index
    与 periods 为剔除后重排的连续序号；totals 为各期关联岗位总数（Top-N 之外
    仍有需求）。
    """
    kept: list[tuple[str | None, list[tuple[str, str, int]], int]] = []
    # (日期, [(岗位id, 岗位名, 出度) Top-N 升序列入, 关联岗位总数])
    skill_name = skill_id
    for v in snapshots:
        snap = v.snapshot_json or {}
        nodes = [
            n for n in snap.get("nodes", [])
            if isinstance(n, dict) and n.get("id") and n.get("type") == "position"
        ]
        names = {n["id"]: n.get("name") for n in nodes}
        pos_ids = set(names)
        for n in snap.get("nodes", []):
            if isinstance(n, dict) and n.get("id") == skill_id and n.get("name"):
                skill_name = n["name"]
                break
        edges = _requires_edges(snap.get("edges", []))
        # 出度按岗位节点过滤：旧快照无 relation 标注时，技能→技能边
        # （source=sk_ 前缀）不得计入岗位出度（P1-2 同根）
        degree = Counter(
            e.get("source") for e in edges if e.get("source") in pos_ids
        )
        assoc = {
            e.get("source")
            for e in edges
            if e.get("target") == skill_id and e.get("source") in pos_ids
        }
        if not assoc:
            continue
        ranked = sorted(
            ((pid, names.get(pid) or pid, degree[pid]) for pid in assoc),
            key=lambda t: (-t[2], t[1]),
        )
        kept.append((
            v.created_at.date().isoformat() if v.created_at else None,
            ranked[:top_n],
            len(assoc),
        ))

    flow_nodes: list[dict] = []
    flow_links: list[dict] = []
    for i, (_, ranked, _total) in enumerate(kept):
        for pid, name, deg in ranked:
            flow_nodes.append({
                "id": f"{pid}::{i}",
                "name": name,
                "period_index": i,
                "freq": deg,
            })
    node_ids = {n["id"] for n in flow_nodes}
    for i in range(len(kept) - 1):
        for pid, _name, deg in kept[i][1]:
            cur, nxt = f"{pid}::{i}", f"{pid}::{i + 1}"
            if cur in node_ids and nxt in node_ids:
                flow_links.append({"source": cur, "target": nxt, "value": deg})

    return {
        "skill_id": skill_id,
        "skill_name": skill_name,
        "periods": [k[0] for k in kept],
        "totals": [k[2] for k in kept],
        "top": top_n,
        "nodes": flow_nodes,
        "links": flow_links,
    }


@router.get("/skill/{id}/flow")
async def skill_flow(
    id: str,
    top: int = Query(default=8, ge=1, le=20, description="每期取出度 Top-N 岗位"),
    db: AsyncSession = Depends(get_db),
    user: Optional[dict] = Depends(get_optional_user),
):
    """技能关联岗位动态变迁桑基图（列=非空快照期次，节点=当期出度 Top-N 岗位，连线=相邻期同名岗位，粗细=岗位要求技能数）。"""
    cache_key = f"evolution:flow:{id}:{top}"
    cached = await _cache_get_json(cache_key)
    if cached is not None:
        return ok(data=cached)

    snapshots = await _load_snapshots(db)
    if snapshots is None:
        return error(ERR_NOT_FOUND, "无图谱版本数据", http_status=404)
    data = _build_skill_flow(snapshots, id, top)
    await _cache_set_json(cache_key, data)
    return ok(data=data)


@router.get("/state-machine")
async def state_machine_overview(
    db: AsyncSession = Depends(get_db),
    user: Optional[dict] = Depends(get_optional_user),
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
            "created_at": iso(log.created_at),
        }
        for log in logs
    ]
    # 待审核规模与审核操作者不外泄：对齐图谱域单一事实源
    # （services/graph/visibility.py）——匿名与 guest 均走 public scope
    # （第七轮审查 P1-4，原实现只防匿名）
    if not visibility._can_view_all_positions(user):
        counts["candidate"] = 0
        transitions = [
            {**t, "operator": "审核员"} for t in transitions if t["to_state"] != "candidate"
        ]
    return ok(data={"states": counts, "transitions": transitions})


@router.get("/watch")
async def technology_watch_overview(
    db: AsyncSession = Depends(get_db),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=10, ge=1, le=100),
    user: Optional[dict] = Depends(get_optional_user),
):
    """观察池公开摘要 + MLI 产业化拐点排名（设计文档 §7.2.5，前端看板）。

    按技能聚合 technology_watch 各源最新信号值，用 MLI（媒介落差指数，四维
    等权）排序输出分页；mli > 0.6 标记 ready_to_industrialize。
    数据来自 watch_signal_daily 每日任务，仅返回公开摘要（无审核队列细节）。
    08-16：limit 改 page/size 分页（演化看板翻页，10 项一页）。
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
            "last_signal_at": iso(info.get("last_signal_at")),
        })
    items.sort(key=lambda x: (-x["mli"], x["skill_name"]))
    total = len(items)
    page_items = _slice_page(items, page, size)
    return ok(data={"items": page_items, "total": total, "page": page, "size": size})
