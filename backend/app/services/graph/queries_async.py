"""图谱会话级异步 Neo4j 查询函数（P2 热路径 async 驱动迁移产物）。

与 queries.py（sync）一一对应的 async 变体，供 graph API 热路径
（skill_positions / fulltext_search / graph_counts / view）
使用——改走 async_neo4j_driver 直查，不再以 asyncio.to_thread 包同步 IO。

迁移约束：Cypher 文本 / 状态过滤子句 / 分页（SKIP/LIMIT）/ Redis-key 与
TTL 语义 / 结果结构与 sync 版本完全一致，仅把 session.run 改 await、
记录迭代改 async for、single() 改 await。查询逻辑本身（status-clause、
communityId 字段等）与 HEAD 同步版本逐字相同。

性能约束（08-18 压测对比发现，源自已删除的 panorama 全图查询）：大结果集
**记录到 dict 的映射是 CPU 密集**——协程内同步映射会阻塞 API 事件循环。
大结果集查询应 await 物化记录（网络等待）后把映射放 asyncio.to_thread。
"""


from app.services.graph.visibility import (
    _PUBLIC_POSITION_STATUSES,
)


async def query_skill_positions(session, skill_id: str, status_filter: str) -> list[dict]:
    """skill_positions 异步查询（状态过滤 + weight 降序，与 sync 一致）。"""
    rows = await session.run(
        f"""
        MATCH (p:Position)-[r:REQUIRES]->(s:Skill {{id: $skill_id}})
        WHERE {status_filter}
        RETURN p.id AS position_id, p.name AS position_name,
               r.necessity AS necessity, r.weight AS weight, r.level AS level
        ORDER BY r.weight DESC
        """,
        skill_id=skill_id, public_statuses=list(_PUBLIC_POSITION_STATUSES),
    )
    items: list[dict] = []
    async for rec in rows:
        items.append({
            "position_id": rec["position_id"],
            "position_name": rec.get("position_name", rec["position_id"]),
            "necessity": rec.get("necessity", "must"),
            "weight": rec.get("weight", 0.0),
            "level": rec.get("level", "中级"),
        })
    return items


async def query_fulltext_search(
    session, q: str, type_: str, status_clause: str, offset: int, size: int,
) -> tuple[list[dict], int]:
    """fulltext_search 异步查询（全文索引 + evidence 降级，与 sync 一致）。"""
    if type_ in ("position", "skill"):
        index = "position_search" if type_ == "position" else "skill_search"
        result = await session.run(
            f"""
            CALL db.index.fulltext.queryNodes('{index}', $q) YIELD node, score
            {status_clause}
            RETURN node.id AS id, node.name AS name, score
            ORDER BY score DESC SKIP $offset LIMIT $size
            """,
            q=q, offset=offset, size=size,
            public_statuses=list(_PUBLIC_POSITION_STATUSES) if status_clause else None,
        )
        total_row = await (await session.run(
            f"""
            CALL db.index.fulltext.queryNodes('{index}', $q) YIELD node
            {status_clause}
            RETURN count(node) AS c
            """,
            q=q,
            public_statuses=list(_PUBLIC_POSITION_STATUSES) if status_clause else None,
        )).single()
        total = total_row["c"] if total_row else 0
    else:
        try:
            result = await session.run(
                "CALL db.index.fulltext.queryNodes('evidence_search', $q) "
                "YIELD node, score "
                "RETURN node.id AS id, node.source AS name, score "
                "ORDER BY score DESC SKIP $offset LIMIT $size",
                q=q, offset=offset, size=size,
            )
            total_row = await (await session.run(
                "CALL db.index.fulltext.queryNodes('evidence_search', $q) "
                "YIELD node RETURN count(node) AS c",
                q=q,
            )).single()
        except Exception:
            result = await session.run(
                """
                MATCH (e:Evidence)
                WHERE e.source CONTAINS $q OR e.raw_text CONTAINS $q
                RETURN e.id AS id, e.source AS name, 0.0 AS score
                ORDER BY id SKIP $offset LIMIT $size
                """,
                q=q, offset=offset, size=size,
            )
            total_row = await (await session.run(
                """
                MATCH (e:Evidence)
                WHERE e.source CONTAINS $q OR e.raw_text CONTAINS $q
                RETURN count(e) AS c
                """,
                q=q,
            )).single()
        total = total_row["c"] if total_row else 0
    items: list[dict] = []
    async for rec in result:
        items.append({
            "id": rec["id"],
            "name": rec.get("name", rec["id"]),
            "type": type_,
            "score": round(rec["score"], 4),
        })
    return items, total


async def query_graph_counts(session) -> dict:
    """图谱全量节点/边数（stats.total_*，与 sync 一致）。"""
    n_row = await (await session.run(
        "MATCH (n) WHERE n:Skill OR n:Position RETURN count(n) AS c")).single()
    n = n_row["c"]
    e_row = await (await session.run(
        "MATCH (:Skill)-[r:REQUIRES]->(:Position) RETURN count(r) AS c")).single()
    e = e_row["c"]
    # 反向边（Position→Skill 是主方向，REQUIRES 可能双向存在，取 max 防重复口径）
    e2_row = await (await session.run(
        "MATCH (:Position)-[r:REQUIRES]->(:Skill) RETURN count(r) AS c")).single()
    e2 = e2_row["c"]
    return {"total_nodes": n, "total_edges": max(e, e2)}


async def query_view_techstack(session, limit: int, status_filter: str) -> list:
    """techStack 视图异步查询（技能频次排序 + 状态过滤，与 sync 一致）。"""
    result = await session.run(
        f"""
        MATCH (s:Skill)<-[r:REQUIRES]-(p:Position)
        WHERE {status_filter}
        WITH s, count(p) AS heat
        ORDER BY heat DESC LIMIT $limit
        MATCH (s)<-[r:REQUIRES]-(p:Position)
        WHERE {status_filter}
        RETURN s.id AS sid, s.name AS sname, s.community_id AS s_community,
               s.category AS s_category,
               p.id AS pid, p.name AS pname, p.status AS pstatus, p.community_id AS p_community, r
        """,
        limit=limit, public_statuses=list(_PUBLIC_POSITION_STATUSES),
    )
    # 08-18 修复：与 panorama 同坑——async driver 的 data() 会把 Relationship
    # 反序列化为 tuple，路由层 record["r"].get() 崩 500；fetch() 保留 Record
    return await result.fetch(100000)


async def query_view_main(session, limit: int, status_filter: str) -> list:
    """positionCenter/level/panorama 视图异步查询（与 sync 一致）。"""
    result = await session.run(
        f"""
        MATCH (p:Position)
        WHERE {status_filter}
        WITH p ORDER BY coalesce(p.freq, 0) DESC, p.name LIMIT $limit
        MATCH (p)-[r:REQUIRES]->(s:Skill)
        RETURN p, s, r
        """,
        limit=limit, public_statuses=list(_PUBLIC_POSITION_STATUSES),
    )
    # 08-18 修复：data() 的 tuple 关系会导致路由映射崩 500（同 panorama 坑）
    return await result.fetch(100000)
