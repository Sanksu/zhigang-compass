"""岗位演化关系推导（设计文档 §5.1 EVOLVED_FROM）。

历史版本快照（graph_versions.snapshot_json，仅含 {id, name, type}）之间，
同一岗位改名/演化为新名称时，会表现为「旧名节点消失 + 新名节点出现」。
本模块基于相邻版本快照的 Position 节点集合差，用保守的命名包含规则
推导 `(新岗位)-[:EVOLVED_FROM {version, change_type}]->(旧岗位)` 边。

规则（宁缺毋滥，避免噪音边）：
- 新名完整包含旧名（长度差 ≥ 2，如"前端工程师" → "前端开发工程师"）→ rename
- 新名与旧名共享 ≥ 2 个连续中文片段（如"数据分析" → "大数据分析"）→ split
- 其余情况不建边（无明确证据的岗位不推断演化关系）

快照是全量导出（graph_version._export_neo4j），旧岗位节点已从当前图谱消失，
故建边前先按快照 id MERGE 重建旧节点并标记 `status='legacy'`（非活跃），
再建演化边——否则 MATCH 旧节点必然 0 行，演化边永远建不出来。
重建与建边均幂等（MERGE），可安全重跑；dry_run 与真实执行共用同一候选逻辑，
结果口径一致。
"""


from sqlalchemy import select

from app.core.database import async_session_factory
from app.models.business import GraphVersion


def _name_containment(new: str, old: str) -> bool:
    """新名完整包含旧名且长度差 ≥ 2（rename 信号）。"""
    return len(new) > len(old) and old in new and len(new) - len(old) >= 2


# 岗位名通用词片段：几乎所有工程师岗名都含，不参与 split 判定——
# 否则任意"X工程师" vs "Y工程师"共享"工程/程师"即误建演化边（08-14 测试发现）。
_GENERIC_NAME_SEGMENTS = frozenset({"工程", "程师", "开发", "分析", "研究", "专员"})


def _shared_segments(new: str, old: str, seg_len: int = 2) -> int:
    """新名与旧名共享的连续片段数（split 信号，按片段去重计数）。

    与 old_segments 取交集后计数，避免 new 中重复出现的同一片段被重复累计
    （如"数据数据" vs "数据"：片段"数据"只算 1 次）。通用岗位词片段
    （工程/程师/开发/分析等）排除——防"运维工程师"vs"算法工程师"式误判。
    """
    old_segments = {old[i : i + seg_len] for i in range(len(old) - seg_len + 1)}
    new_segments = {new[i : i + seg_len] for i in range(len(new) - seg_len + 1)}
    shared = new_segments & old_segments
    return len(shared - _GENERIC_NAME_SEGMENTS)


def _position_nodes(snapshot: dict) -> dict[str, str]:
    """快照中 Position 节点 id → name 映射。"""
    return {
        n["id"]: n["name"]
        for n in (snapshot or {}).get("nodes", [])
        if n.get("type") == "position" and n.get("name")
    }


async def derive_evolved_from(dry_run: bool = False) -> dict:
    """基于最近两个版本快照推导 EVOLVED_FROM 边（幂等 MERGE）。

    旧岗位节点已从当前图谱消失（快照全量导出），建边前按快照 id 重建为
    `status='legacy'` 节点，再建 `(新)-[:EVOLVED_FROM]->(旧)` 边。
    dry_run 与真实执行候选逻辑一致（edges 即候选命中数）。

    Returns:
        {"versions": [v_prev, v_cur], "edges": n, "skipped": n}
    """
    from app.core.database import neo4j_driver

    async with async_session_factory() as session:
        versions = (await session.scalars(
            select(GraphVersion).order_by(GraphVersion.created_at.asc())
        )).all()
    if len(versions) < 2:
        return {"versions": [], "edges": 0, "skipped": 0, "detail": "快照不足 2 个版本"}

    prev_snap, cur_snap = versions[-2].snapshot_json, versions[-1].snapshot_json
    cur_version = versions[-1].id
    prev_nodes = _position_nodes(prev_snap)   # {id: name}
    cur_nodes = _position_nodes(cur_snap)
    prev_names = set(prev_nodes.values())
    cur_names = set(cur_nodes.values())
    new_names = cur_names - prev_names
    gone_names = prev_names - cur_names
    if not new_names or not gone_names:
        return {"versions": [versions[-2].id, cur_version], "edges": 0, "skipped": 0}

    prev_name_to_id = {name: nid for nid, name in prev_nodes.items()}
    cur_name_to_id = {name: nid for nid, name in cur_nodes.items()}

    edges = 0
    skipped = 0
    for new in new_names:
        new_id = cur_name_to_id.get(new)
        if new_id is None:
            continue
        for old in gone_names:
            old_id = prev_name_to_id.get(old)
            if _name_containment(new, old):
                change_type = "rename"
            elif _shared_segments(new, old) >= 2:
                change_type = "split"
            else:
                skipped += 1
                continue
            if old_id is None:
                skipped += 1
                continue
            if dry_run:
                edges += 1
                continue
            with neo4j_driver.session() as ns:
                result = ns.run(
                    """
                    // 旧岗位已从当前图谱消失（快照全量导出），按快照 id 重建为 legacy 节点后再建边
                    MERGE (b:Position {id: $old_id})
                    SET b.name = $old, b.status = 'legacy'
                    WITH b
                    MATCH (a:Position {id: $new_id})
                    MERGE (a)-[r:EVOLVED_FROM]->(b)
                    SET r.version = $version, r.change_type = $change_type
                    RETURN count(r) AS covered
                    """,
                    old_id=old_id, old=old, new_id=new_id,
                    version=cur_version, change_type=change_type,
                ).single()
                edges += int(result["covered"]) if result else 0

    return {
        "versions": [versions[-2].id, cur_version],
        "edges": edges,
        "skipped": skipped,
        "new_positions": len(new_names),
        "gone_positions": len(gone_names),
    }
