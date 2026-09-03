"""图谱会话级异步 Neo4j 查询函数（P2 热路径 async 驱动迁移产物）。

与 queries.py（sync）一一对应的 async 变体，供 graph API 热路径
（skill_positions / fulltext_search / graph_counts / view）
使用——改走 async_neo4j_driver 直查，不再以 asyncio.to_thread 包同步 IO。

迁移约束：Cypher 文本 / 状态过滤子句 / 分页（SKIP/LIMIT）/ Redis-key 与
TTL 语义 / 结果结构与 sync 版本完全一致，仅把 session.run 改 await、
记录迭代改 async for、single() 改 await。查询逻辑（status-clause 等）
与 HEAD 同步版本逐字相同。

性能约束（08-18 压测对比发现，源自已删除的 panorama 全图查询）：大结果集
**记录到 dict 的映射是 CPU 密集**——协程内同步映射会阻塞 API 事件循环。
大结果集查询应 await 物化记录（网络等待）后把映射放 asyncio.to_thread。
"""


from app.services.graph.queries import _escape_lucene
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
    """fulltext_search 异步查询（全文索引 + evidence 降级，与 sync 一致）。

    q 进 queryNodes 前经 _escape_lucene 转义（第八轮 P2-5，与 sync 版一致：
    `("` 等 Lucene 语法字符触发 GqlError→500）；evidence 降级 CONTAINS
    用原文（子串匹配，转义反而失真）。
    """
    lq = _escape_lucene(q)
    if type_ in ("position", "skill"):
        index = "position_search" if type_ == "position" else "skill_search"
        # 2026-09-02 P99 优化：数据页与 total 合并为单次 fulltext 索引调用
        #（原为同索引查询跑两遍：数据 + count）。fulltext 命中集有限（几十~几百），
        # collect 全集再内存分页与旧两查结果一致（同分节点排列可不同，均正确）。
        result = await session.run(
            f"""
            CALL db.index.fulltext.queryNodes('{index}', $lq) YIELD node, score
            {status_clause}
            ORDER BY score DESC
            WITH collect({{id: node.id, name: node.name, score: score}}) AS hits,
                 count(node) AS total
            RETURN total,
                   [x IN range($offset, $offset + $size - 1) WHERE x < size(hits) | hits[x]] AS page
            """,
            lq=lq, offset=offset, size=size,
            public_statuses=list(_PUBLIC_POSITION_STATUSES) if status_clause else None,
        )
        record = await result.single()
        if record is None:
            return [], 0
        return record["page"], record["total"]
    else:
        try:
            result = await session.run(
                "CALL db.index.fulltext.queryNodes('evidence_search', $lq) "
                "YIELD node, score "
                "ORDER BY score DESC "
                "WITH collect({id: node.id, name: node.source, score: score}) AS hits, "
                "count(node) AS total "
                "RETURN total, [x IN range($offset, $offset + $size - 1) "
                "WHERE x < size(hits) | hits[x]] AS page",
                lq=lq, offset=offset, size=size,
            )
            record = await result.single()
            if record is None:
                return [], 0
            return record["page"], record["total"]
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


def _level_clause(level: str | None) -> str:
    """熟练度级别过滤子句（REQUIRES.level；缺省不过滤）。"""
    return "AND coalesce(r.level, '') = $level" if level else ""


async def query_view_techstack(
    session, limit: int, status_filter: str, level: str | None = None
) -> list:
    """techStack 视图异步查询（技能频次排序 + 状态/级别过滤，与 sync 一致）。"""
    params = {"limit": limit, "public_statuses": list(_PUBLIC_POSITION_STATUSES)}
    if level:
        params["level"] = level
    result = await session.run(
        f"""
        MATCH (s:Skill)<-[r:REQUIRES]-(p:Position)
        WHERE {status_filter} {_level_clause(level)}
        WITH s, count(p) AS heat
        ORDER BY heat DESC LIMIT $limit
        MATCH (s)<-[r:REQUIRES]-(p:Position)
        WHERE {status_filter} {_level_clause(level)}
        RETURN s.id AS sid, s.name AS sname,
               s.category AS s_category,
               p.id AS pid, p.name AS pname, p.status AS pstatus, r
        """,
        **params,
    )
    # 08-18 修复：与 panorama 同坑——async driver 的 data() 会把 Relationship
    # 反序列化为 tuple，路由层 record["r"].get() 崩 500；fetch() 保留 Record
    return await result.fetch(100000)


async def query_view_main(
    session, limit: int, status_filter: str, level: str | None = None
) -> list:
    """positionCenter/level/panorama 视图异步查询（与 sync 一致）。"""
    params = {"limit": limit, "public_statuses": list(_PUBLIC_POSITION_STATUSES)}
    if level:
        params["level"] = level
    # MATCH 后无既有 WHERE，级别子句需自带 WHERE 关键字
    level_clause = "WHERE coalesce(r.level, '') = $level" if level else ""
    result = await session.run(
        f"""
        MATCH (p:Position)
        WHERE {status_filter}
        WITH p ORDER BY coalesce(p.freq, 0) DESC, p.name LIMIT $limit
        MATCH (p)-[r:REQUIRES]->(s:Skill)
        {level_clause}
        RETURN p, s, r
        """,
        **params,
    )
    # 08-18 修复：data() 的 tuple 关系会导致路由映射崩 500（同 panorama 坑）
    return await result.fetch(100000)


async def query_view_position_portrait(session, position_id: str, limit: int, status_filter: str) -> list:
    """岗位画像视图查询：以指定岗位为中心的画像图谱。

    返回单行（fetch Record）：p（岗位本体，含画像属性）+ skills 列表
    （按 r.weight 降序 Top-limit，元素 {sid, sname, scat, weight, necessity, level}）。
    匿名/guest 对 candidate/archived 岗位返回空行（_position_scope 过滤）。
    """
    result = await session.run(
        f"""
        MATCH (p:Position)
        WHERE (p.id = $pid OR p.name = $pid) AND {status_filter}
        OPTIONAL MATCH (p)-[r:REQUIRES]->(s:Skill)
        WITH p, r, s ORDER BY coalesce(r.weight, 0) DESC
        WITH p, collect({{sid: s.id, sname: s.name, scat: s.category,
                          weight: coalesce(r.weight, 0),
                          necessity: coalesce(r.necessity, 'must'),
                          level: r.level,
                          scount: coalesce(r.source_count, 1)}})[0..$limit] AS skills
        RETURN p, skills
        """,
        pid=position_id, limit=limit,
        public_statuses=list(_PUBLIC_POSITION_STATUSES),
    )
    return await result.fetch(10000)
