"""图谱会话级 Neo4j 查询函数（graph.py 查询逻辑拆分产物）。

本模块只接收 session 做查询，不管理驱动/会话（驱动与会话生命周期归
repository.py）。Cypher / 状态过滤子句 / 结果结构与原 graph.py 完全一致，
repository 层开 session 后在此批量执行。

- group A：技能反向查询 / 全文检索
- group B：岗位技能 / 先修链 / 计数 / 证据 / 最短路径 / 视图查询 / 节点加载
"""

from app.services.graph.visibility import (
    _PUBLIC_POSITION_STATUSES,
    _position_scope,
    _status_clause,
)
from app.services.graph_algorithms.shortest_path import shortest_path
from app.services.kg.skill_relations import graph_prerequisite_chain

# Lucene 全文查询特殊字符（db.index.fulltext.queryNodes 的查询语法）：
# 用户输入含这些字符时会被当作查询操作符——轻则语法报错（GqlError→500），
# 重则被构造成通配/范围查询拖垮索引（第八轮审查 P2-5）。
_LUCENE_SPECIAL_CHARS = set('+-!(){}[]^"~*?:\\/')


def _escape_lucene(q: str) -> str:
    """转义 Lucene 查询语法特殊字符（对齐 Lucene QueryParser.escape 语义）。

    - 单特殊字符（`+ - ! ( ) { } [ ] ^ " ~ * ? : \\ /`）前加反斜杠；
    - `&&` / `||` 操作符转义首个字符（单 `&` / `|` 非法不构成操作符，不动）；
    - 其余字符（含中文、空格）原样保留——cjk 分词器按原词切分，转义不影响。
    """
    out: list[str] = []
    for i, ch in enumerate(q):
        if ch == "\\":
            out.append("\\\\")
        elif ch in _LUCENE_SPECIAL_CHARS:
            out.append("\\" + ch)
        elif ch in "&|" and q[i + 1 : i + 2] == ch:
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


def query_skill_positions(session, skill_id: str, status_filter: str) -> list[dict]:
    """skill_positions 同步 Neo4j 查询（线程池执行）。"""
    rows = session.run(
        f"""
        MATCH (p:Position)-[r:REQUIRES]->(s:Skill {{id: $skill_id}})
        WHERE {status_filter}
        RETURN p.id AS position_id, p.name AS position_name,
               r.necessity AS necessity, r.weight AS weight, r.level AS level
        ORDER BY r.weight DESC
        """,
        skill_id=skill_id, public_statuses=list(_PUBLIC_POSITION_STATUSES),
    )
    return [
        {
            "position_id": rec["position_id"],
            "position_name": rec.get("position_name", rec["position_id"]),
            "necessity": rec.get("necessity", "must"),
            "weight": rec.get("weight", 0.0),
            "level": rec.get("level", "中级"),
        }
        for rec in rows
    ]


def query_fulltext_search(
    session, q: str, type_: str, status_clause: str, offset: int, size: int,
) -> tuple[list[dict], int]:
    """fulltext_search 同步 Neo4j 查询（线程池执行，08-14 审查）。

    q 进 queryNodes 前经 _escape_lucene 转义（第八轮 P2-5：`("` 等 Lucene
    语法字符触发 GqlError→500）；evidence 降级 CONTAINS 用原文（子串匹配，
    转义反而失真）。
    """
    lq = _escape_lucene(q)
    if type_ in ("position", "skill"):
        index = "position_search" if type_ == "position" else "skill_search"
        result = session.run(
            f"""
            CALL db.index.fulltext.queryNodes('{index}', $lq) YIELD node, score
            {status_clause}
            RETURN node.id AS id, node.name AS name, score
            ORDER BY score DESC SKIP $offset LIMIT $size
            """,
            lq=lq, offset=offset, size=size,
            public_statuses=list(_PUBLIC_POSITION_STATUSES) if status_clause else None,
        )
        total_row = session.run(
            f"""
            CALL db.index.fulltext.queryNodes('{index}', $lq) YIELD node
            {status_clause}
            RETURN count(node) AS c
            """,
            lq=lq,
            public_statuses=list(_PUBLIC_POSITION_STATUSES) if status_clause else None,
        ).single()
        total = total_row["c"] if total_row else 0
    else:
        try:
            result = session.run(
                "CALL db.index.fulltext.queryNodes('evidence_search', $lq) "
                "YIELD node, score "
                "RETURN node.id AS id, node.source AS name, score "
                "ORDER BY score DESC SKIP $offset LIMIT $size",
                lq=lq, offset=offset, size=size,
            )
            total_row = session.run(
                "CALL db.index.fulltext.queryNodes('evidence_search', $lq) "
                "YIELD node RETURN count(node) AS c",
                lq=lq,
            ).single()
        except Exception:
            result = session.run(
                """
                MATCH (e:Evidence)
                WHERE e.source CONTAINS $q OR e.raw_text CONTAINS $q
                RETURN e.id AS id, e.source AS name, 0.0 AS score
                ORDER BY id SKIP $offset LIMIT $size
                """,
                q=q, offset=offset, size=size,
            )
            total_row = session.run(
                """
                MATCH (e:Evidence)
                WHERE e.source CONTAINS $q OR e.raw_text CONTAINS $q
                RETURN count(e) AS c
                """,
                q=q,
            ).single()
        total = total_row["c"] if total_row else 0
    items = [
        {
            "id": rec["id"],
            "name": rec.get("name", rec["id"]),
            "type": type_,
            "score": round(rec["score"], 4),
        }
        for rec in result
    ]
    return items, total


def query_position_skills_by_necessity(session, id: str) -> dict[str, dict]:
    """岗位技能（按 necessity 分组，线程池执行，08-14 审查）。"""
    skills: dict[str, dict] = {}
    rows = session.run(
        """
        MATCH (p:Position {id: $id})-[r:REQUIRES]->(s:Skill)
        RETURN s.id AS skill_id, s.name AS skill_name,
               r.necessity AS necessity, r.weight AS weight,
               r.level AS level, r.source_count AS source_count,
               s.category AS skill_category
        ORDER BY r.weight DESC
        """,
        id=id,
    )
    for rec in rows:
        necessity = rec.get("necessity", "must")
        skills.setdefault(necessity, []).append({
            "skill_id": rec["skill_id"],
            "skill_name": rec.get("skill_name", rec["skill_id"]),
            "necessity": necessity,
            "weight": rec.get("weight", 0.0),
            "level": rec.get("level", "中级"),
            "source_count": rec.get("source_count", 1),
            "skill_category": rec.get("skill_category"),
        })
    return skills


def find_position_id_by_name(session, name: str) -> str | None:
    """按岗位名反查图谱岗位 id（Position.name 唯一约束，见 schema.cypher）。

    供「新岗位发现」候选按 position_name 回查技能用。无匹配返回 None。
    """
    rec = session.run(
        "MATCH (p:Position {name: $name}) RETURN p.id AS id LIMIT 1",
        name=name,
    ).single()
    return rec["id"] if rec else None


def query_prereq_chain(session, skill_name: str) -> list[str]:
    """图谱先修链（线程池执行，08-14 审查）。"""
    return graph_prerequisite_chain(session, skill_name)


def query_skill_ids(session, names: list[str]) -> dict[str, str]:
    """技能名 → 图谱 ID（线程池执行）。"""
    rows = session.run(
        "MATCH (s:Skill) WHERE s.name IN $names RETURN s.name AS name, s.id AS id",
        names=names,
    )
    return {rec["name"]: rec["id"] for rec in rows}


def query_all_skills(session) -> list[tuple[str, str]]:
    """全技能 (id, name)（线程池执行）。"""
    rows = session.run("MATCH (s:Skill) RETURN s.id AS id, s.name AS name")
    return [(rec["id"], rec.get("name", rec["id"])) for rec in rows]


def query_graph_counts(session) -> dict:
    """图谱全量节点/边数（stats.total_*，线程池执行，08-14 契约补全）。"""
    n = session.run(
        "MATCH (n) WHERE n:Skill OR n:Position RETURN count(n) AS c").single()["c"]
    e = session.run("MATCH (:Skill)-[r:REQUIRES]->(:Position) RETURN count(r) AS c").single()["c"]
    # 反向边（Position→Skill 是主方向，REQUIRES 可能双向存在，取 max 防重复口径）
    e2 = session.run("MATCH (:Position)-[r:REQUIRES]->(:Skill) RETURN count(r) AS c").single()["c"]
    return {"total_nodes": n, "total_edges": max(e, e2)}


def query_skill_evidence(session, skill_id: str) -> list[dict]:
    """技能证据列表（线程池执行，08-14 低优先批次）。"""
    rows = session.run(
        """
        MATCH (s:Skill {id: $skill_id})-[:EVIDENCED_BY]->(e:Evidence)
        RETURN e.id AS id, e.source AS source, e.source_url AS source_url,
               e.crawled_at AS crawled_at
        ORDER BY e.crawled_at DESC
        """,
        skill_id=skill_id,
    )
    return [
        {
            "id": rec["id"],
            "source": rec.get("source", ""),
            "source_url": rec.get("source_url", ""),
            "crawled_at": rec.get("crawled_at"),
        }
        for rec in rows
    ]


def query_shortest_path(session, from_skill: str, to_skill: str, statuses) -> list | None:
    """最短路径查询（线程池执行）。"""
    return shortest_path(session, from_skill, to_skill, position_statuses=statuses)


def query_view_techstack(session, limit: int, status_filter: str) -> list:
    """techStack 视图查询（线程池执行，08-14 低优先批次）。"""
    return list(session.run(
        f"""
        MATCH (s:Skill)<-[r:REQUIRES]-(p:Position)
        WHERE {status_filter}
        WITH s, count(p) AS heat
        ORDER BY heat DESC LIMIT $limit
        MATCH (s)<-[r:REQUIRES]-(p:Position)
        WHERE {status_filter}
        RETURN s.id AS sid, s.name AS sname,
               s.category AS s_category,
               p.id AS pid, p.name AS pname, p.status AS pstatus, r
        """,
        limit=limit, public_statuses=list(_PUBLIC_POSITION_STATUSES),
    ))


def query_view_main(session, limit: int, status_filter: str) -> list:
    """positionCenter/level/panorama 视图查询（线程池执行）。"""
    return list(session.run(
        f"""
        MATCH (p:Position)
        WHERE {status_filter}
        WITH p ORDER BY coalesce(p.freq, 0) DESC, p.name LIMIT $limit
        MATCH (p)-[r:REQUIRES]->(s:Skill)
        RETURN p, s, r
        """,
        limit=limit, public_statuses=list(_PUBLIC_POSITION_STATUSES),
    ))


def load_skill(session, skill_id: str) -> dict | None:
    """按 ID 查询技能节点（id + name + category），不存在返回 None。"""
    rec = session.run(
        "MATCH (s:Skill {id: $skill_id}) "
        "RETURN s.id AS id, s.name AS name, s.category AS category",
        skill_id=skill_id,
    ).single()
    return dict(rec) if rec else None


def load_position(session, id: str, user=None) -> dict | None:
    """按 ID 查询岗位节点基础属性（不含技能边），不存在返回 None。

    user/admin 可见全部岗位；匿名/guest 对 candidate/archived 岗位返回 None
    （视为不存在，避免待审核岗位外泄，见方案一）。
    """
    scope = _position_scope(user)
    status_filter = _status_clause(scope)
    rec = session.run(
        f"""
        MATCH (p:Position {{id: $id}})
        WHERE {status_filter}
        RETURN p.id AS id, p.name AS name, p.required_years AS required_years,
               p.required_education AS required_education, p.last_updated AS last_updated,
               p.status AS status, p.freq AS freq, p.soft_skills AS soft_skills,
               p.evidence_count AS evidence_count,
               p.education_distribution AS education_distribution,
               p.experience_distribution AS experience_distribution,
               p.salary_tiers AS salary_tiers, p.salary_min AS salary_min,
               p.salary_max AS salary_max, p.salary_currency AS salary_currency,
               p.salary_range AS salary_range
        """,
        id=id, public_statuses=list(_PUBLIC_POSITION_STATUSES),
    ).single()
    return dict(rec) if rec else None
