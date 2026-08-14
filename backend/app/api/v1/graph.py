"""图谱路由：全景、技能反向查询、全文检索、先修链、学习课程。"""

import asyncio
import json
import logging
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_optional_user, role_rank
from app.core.database import get_db, neo4j_driver, redis_client
from app.models.business import SkillEmbedding
from app.schemas.common import error, ok
from app.services.graph_algorithms.config import load_graph_algo_config
from app.services.graph_algorithms.shortest_path import shortest_path
from app.services.kg.skill_relations import graph_prerequisite_chain
from app.services.learning_path.courses import load_courses_for_skill
from app.services.learning_path.prerequisites import prerequisite_chain
from app.services.matching.semantic import SkillEmbedder, SemanticUnavailableError

logger = logging.getLogger(__name__)

router = APIRouter()

# 全景查询缓存 TTL（设计文档 10.3：panorama 短 TTL 30s）
PANORAMA_CACHE_TTL = 30

# 节点详情缓存 TTL（设计文档 §11.3.5：position:{id} 5min，skill 同档）
_NODE_CACHE_TTL = 300

# 匿名/guest 可见的岗位状态（方案一：candidate 待审核不外宣，archived 已下线）
_PUBLIC_POSITION_STATUSES = ("emerging", "stable", "declining")


def _can_view_all_positions(user: Optional[dict]) -> bool:
    """user/admin 可见全部岗位；匿名/guest 只见 emerging/stable/declining。"""
    return user is not None and role_rank(user) >= role_rank({"role": "user"})


def _position_scope(user: Optional[dict]) -> str:
    """缓存 key 的可见性维度：all=全量（user/admin），public=仅公开态。"""
    return "all" if _can_view_all_positions(user) else "public"


async def _cache_get(key: str):
    """Redis 缓存读取（JSON 反序列化），未命中返回 None。"""
    cached = await redis_client.get(key)
    return json.loads(cached) if cached else None


async def _cache_set(key: str, data, ttl: int = _NODE_CACHE_TTL) -> None:
    """Redis 缓存写入（JSON 序列化）。"""
    await redis_client.set(key, json.dumps(data), ex=ttl)


@router.get("/panorama")
def _query_panorama(scope: str, focus: str | None, min_weight: float, limit: int) -> tuple[dict, list]:
    """panorama 同步 Neo4j 查询（08-14 审查：原在 async 函数内同步阻塞事件循环，抽到线程池）。"""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    status_filter = "p.status IN $public_statuses" if scope == "public" else "true"
    with neo4j_driver.session() as session:
        if focus:
            rows = session.run(
                f"""
                MATCH (p:Position {{id: $focus}})-[r:REQUIRES]->(s:Skill)
                WHERE {status_filter} AND r.weight >= $min_weight
                RETURN p, s, r
                """,
                focus=focus, min_weight=min_weight, public_statuses=list(_PUBLIC_POSITION_STATUSES),
            )
        else:
            rows = session.run(
                f"""
                MATCH (p:Position)
                WHERE {status_filter}
                WITH p ORDER BY coalesce(p.freq, 0) DESC, p.name LIMIT $limit
                MATCH (p)-[r:REQUIRES]->(s:Skill)
                WHERE r.weight >= $min_weight
                RETURN p, s, r
                """,
                limit=limit, min_weight=min_weight, public_statuses=list(_PUBLIC_POSITION_STATUSES),
            )
        for record in rows:
            p, s, r = record["p"], record["s"], record["r"]
            p_id = p.get("id", "")
            s_id = s.get("id", "")
            nodes.setdefault(p_id, {
                "id": p_id,
                "name": p.get("name", p_id),
                "type": "position",
                "status": p.get("status", "candidate"),
            })
            nodes.setdefault(s_id, {"id": s_id, "name": s.get("name", s_id), "type": "skill"})
            edges.append({
                "source": p_id,
                "target": s_id,
                "weight": r.get("weight", 0.0),
                "necessity": r.get("necessity", "must"),
                "level": r.get("level", "中级"),
            })
    return nodes, edges


def _query_skill_positions(skill_id: str, status_filter: str) -> list[dict]:
    """skill_positions 同步 Neo4j 查询（线程池执行）。"""
    with neo4j_driver.session() as session:
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


def _query_fulltext_search(
    q: str, type_: str, status_clause: str, offset: int, size: int,
) -> tuple[list[dict], int]:
    """fulltext_search 同步 Neo4j 查询（线程池执行，08-14 审查）。"""
    with neo4j_driver.session() as session:
        if type_ in ("position", "skill"):
            index = "position_search" if type_ == "position" else "skill_search"
            result = session.run(
                f"""
                CALL db.index.fulltext.queryNodes('{index}', $q) YIELD node, score
                {status_clause}
                RETURN node.id AS id, node.name AS name, score
                ORDER BY score DESC SKIP $offset LIMIT $size
                """,
                q=q, offset=offset, size=size,
                public_statuses=list(_PUBLIC_POSITION_STATUSES) if status_clause else None,
            )
            total_row = session.run(
                f"""
                CALL db.index.fulltext.queryNodes('{index}', $q) YIELD node
                {status_clause}
                RETURN count(node) AS c
                """,
                q=q,
                public_statuses=list(_PUBLIC_POSITION_STATUSES) if status_clause else None,
            ).single()
            total = total_row["c"] if total_row else 0
        else:
            try:
                result = session.run(
                    "CALL db.index.fulltext.queryNodes('evidence_search', $q) "
                    "YIELD node, score "
                    "RETURN node.id AS id, node.source AS name, score "
                    "ORDER BY score DESC SKIP $offset LIMIT $size",
                    q=q, offset=offset, size=size,
                )
                total_row = session.run(
                    "CALL db.index.fulltext.queryNodes('evidence_search', $q) "
                    "YIELD node RETURN count(node) AS c",
                    q=q,
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


def _query_position_skills_by_necessity(id: str) -> dict[str, dict]:
    """岗位技能（按 necessity 分组，线程池执行，08-14 审查）。"""
    skills: dict[str, dict] = {}
    with neo4j_driver.session() as session:
        rows = session.run(
            """
            MATCH (p:Position {id: $id})-[r:REQUIRES]->(s:Skill)
            RETURN s.id AS skill_id, s.name AS skill_name,
                   r.necessity AS necessity, r.weight AS weight,
                   r.level AS level, r.source_count AS source_count
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
            })
    return skills


def _query_prereq_chain(skill_name: str) -> list[str]:
    """图谱先修链（线程池执行，08-14 审查）。"""
    with neo4j_driver.session() as session:
        return graph_prerequisite_chain(session, skill_name)


def _query_skill_ids(names: list[str]) -> dict[str, str]:
    """技能名 → 图谱 ID（线程池执行）。"""
    with neo4j_driver.session() as session:
        rows = session.run(
            "MATCH (s:Skill) WHERE s.name IN $names RETURN s.name AS name, s.id AS id",
            names=names,
        )
        return {rec["name"]: rec["id"] for rec in rows}


def _query_position_skills(id: str, necessity: str | None, status_filter: str) -> list[dict]:
    """岗位技能（可按 necessity 过滤，线程池执行）。"""
    query = f"""
        MATCH (p:Position {{id: $id}})-[r:REQUIRES]->(s:Skill)
        WHERE ({status_filter}) AND ($necessity IS NULL OR r.necessity = $necessity)
        RETURN s.id AS skill_id, s.name AS skill_name,
               r.necessity AS necessity, r.weight AS weight,
               r.level AS level, r.source_count AS source_count
        ORDER BY r.weight DESC
    """
    with neo4j_driver.session() as session:
        rows = session.run(
            query, id=id, necessity=necessity,
            public_statuses=list(_PUBLIC_POSITION_STATUSES),
        )
        return [
            {
                "skill_id": rec["skill_id"],
                "skill_name": rec.get("skill_name", rec["skill_id"]),
                "necessity": rec.get("necessity", "must"),
                "weight": rec.get("weight", 0.0),
                "level": rec.get("level", "中级"),
                "source_count": rec.get("source_count", 1),
            }
            for rec in rows
        ]


def _query_all_skills() -> list[tuple[str, str]]:
    """全技能 (id, name)（线程池执行）。"""
    with neo4j_driver.session() as session:
        rows = session.run("MATCH (s:Skill) RETURN s.id AS id, s.name AS name")
        return [(rec["id"], rec.get("name", rec["id"])) for rec in rows]


def _query_skill_counts(skill_id: str, status_filter: str) -> dict:
    """技能关联计数（岗位/证据，线程池执行）。"""
    with neo4j_driver.session() as session:
        rec = session.run(
            f"""
            MATCH (s:Skill {{id: $skill_id}})
            OPTIONAL MATCH (p:Position)-[r:REQUIRES]->(s)
            OPTIONAL MATCH (s)-[:EVIDENCED_BY]->(e:Evidence)
            WITH s, e, CASE WHEN {status_filter} THEN p ELSE null END AS visible_p
            RETURN count(DISTINCT visible_p) AS positions_count, count(DISTINCT e) AS evidence_count
            """,
            skill_id=skill_id, public_statuses=list(_PUBLIC_POSITION_STATUSES),
        ).single()
        return dict(rec) if rec else {}


def _query_graph_counts() -> dict:
    """图谱全量节点/边数（stats.total_*，线程池执行，08-14 契约补全）。"""
    with neo4j_driver.session() as session:
        n = session.run(
            "MATCH (n) WHERE n:Skill OR n:Position RETURN count(n) AS c").single()["c"]
        e = session.run("MATCH (:Skill)-[r:REQUIRES]->(:Position) RETURN count(r) AS c").single()["c"]
        # 反向边（Position→Skill 是主方向，REQUIRES 可能双向存在，取 max 防重复口径）
        e2 = session.run("MATCH (:Position)-[r:REQUIRES]->(:Skill) RETURN count(r) AS c").single()["c"]
    return {"total_nodes": n, "total_edges": max(e, e2)}


def _query_skill_evidence(skill_id: str) -> list[dict]:
    """技能证据列表（线程池执行，08-14 低优先批次）。"""
    with neo4j_driver.session() as session:
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


def _query_shortest_path(from_skill: str, to_skill: str, statuses) -> list | None:
    """最短路径查询（线程池执行）。"""
    with neo4j_driver.session() as session:
        return shortest_path(session, from_skill, to_skill, position_statuses=statuses)


def _query_view_techstack(limit: int, status_filter: str) -> list:
    """techStack 视图查询（线程池执行，08-14 低优先批次）。"""
    with neo4j_driver.session() as session:
        return list(session.run(
            f"""
            MATCH (s:Skill)<-[r:REQUIRES]-(p:Position)
            WHERE {status_filter}
            WITH s, count(p) AS heat
            ORDER BY heat DESC LIMIT $limit
            MATCH (s)<-[r:REQUIRES]-(p:Position)
            WHERE {status_filter}
            RETURN s.id AS sid, s.name AS sname, p.id AS pid, p.name AS pname,
                   p.status AS pstatus, r
            """,
            limit=limit, public_statuses=list(_PUBLIC_POSITION_STATUSES),
        ))


def _query_view_main(limit: int, status_filter: str) -> list:
    """positionCenter/level/panorama 视图查询（线程池执行）。"""
    with neo4j_driver.session() as session:
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


async def panorama(
    limit: int = Query(default=100, ge=1, le=600),
    min_weight: float = Query(default=0.3, ge=0.0, le=1.0),
    focus: Optional[str] = Query(default=None),
    user: Optional[dict] = Depends(get_optional_user),
):
    """图谱全景视图（匿名可读，30s Redis TTL 缓存，见设计文档 10.3）。

    focus 缺省时返回 Top-N 高频岗位 + 关联技能；指定 focus 时以该岗位为中心展开。
    匿名/guest 仅返回 emerging/stable/declining 岗位（candidate 待审核不外宣），
    携带有效 token 的 user/admin 返回全量。
    """
    scope = _position_scope(user)
    cache_key = f"graph:panorama:{scope}:{limit}:{min_weight}:{focus or 'all'}"
    cached = await redis_client.get(cache_key)
    if cached is not None:
        return ok(data=json.loads(cached))

    nodes, edges = await asyncio.to_thread(
        _query_panorama, scope, focus, min_weight, limit)

    data = {
        "nodes": list(nodes.values()),
        "edges": edges,
        "stats": {"nodes": len(nodes), "edges": len(edges),
                  **await asyncio.to_thread(_query_graph_counts)},
    }
    await redis_client.set(cache_key, json.dumps(data), ex=PANORAMA_CACHE_TTL)
    return ok(data=data)


@router.get("/skill/{skill_id}/positions")
async def skill_positions(
    skill_id: str,
    user: Optional[dict] = Depends(get_optional_user),
):
    """技能节点反向查询：返回关联的岗位列表 + necessity + weight + level。

    匿名/guest 仅返回 emerging/stable/declining 岗位（candidate 待审核不外宣）。
    """
    scope = _position_scope(user)
    cache_key = f"graph:skill:{skill_id}:positions:{scope}"
    cached = await _cache_get(cache_key)
    if cached is not None:
        return ok(data=cached)
    status_filter = "p.status IN $public_statuses" if scope == "public" else "true"
    positions = await asyncio.to_thread(
        _query_skill_positions, skill_id, status_filter)
    data = {"skill_id": skill_id, "positions": positions}
    await _cache_set(cache_key, data)
    return ok(data=data)


@router.get("/search")
async def fulltext_search(
    q: str = Query(..., min_length=1),
    type_: str = Query(default="position", alias="type", enum=["position", "skill", "evidence"]),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    user: Optional[dict] = Depends(get_optional_user),
):
    """Neo4j 全文检索（匿名可读，cjk 分词器，设计文档 5.4）。

    position/skill 走全文索引；evidence 走 evidence_search 全文索引
    （M17 新增，索引缺失时降级 CONTAINS）。
    匿名/guest 检索岗位时排除 candidate（待审核不外宣），user/admin 含全量。
    """
    scope = _position_scope(user)
    offset = (page - 1) * size
    items: list[dict] = []
    total = 0
    # 匿名/guest 检索岗位时排除 candidate（全文索引 YIELD 的是完整节点，可直接过滤）
    status_clause = (
        "WHERE node.status IN $public_statuses" if scope == "public" and type_ == "position" else ""
    )

    items, total = await asyncio.to_thread(
        _query_fulltext_search, q, type_, status_clause, offset, size)

    return ok(data={"items": items, "total": total, "page": page, "size": size})


def _load_skill(skill_id: str) -> dict | None:
    """按 ID 查询技能节点（id + name），不存在返回 None。"""
    with neo4j_driver.session() as session:
        rec = session.run(
            "MATCH (s:Skill {id: $skill_id}) RETURN s.id AS id, s.name AS name",
            skill_id=skill_id,
        ).single()
    return dict(rec) if rec else None


@router.get("/skill/{skill_id}/prerequisites")
async def skill_prerequisites(skill_id: str):
    """技能先修技能链（AL-M4-03，设计文档 §9.5）。

    先修链优先走图谱 PREREQUISITE_OF 边（skill_relations 字典同步产物），
    图谱未建边时回退人工维护字典 configs/skill_prerequisites.yaml；
    返回拓扑序（先修在前），并富化图谱技能 ID。
    """
    skill = await asyncio.to_thread(_load_skill, skill_id)
    if skill is None:
        return error(4040, "技能不存在", http_status=404)

    chain = await asyncio.to_thread(_query_prereq_chain, skill["name"])
    if not chain:
        chain = prerequisite_chain(skill["name"])
    id_by_name: dict[str, str] = {}
    if chain:
        id_by_name = await asyncio.to_thread(_query_skill_ids, chain)
    prerequisites = [
        {"skill_id": id_by_name.get(name), "name": name, "depth": i + 1}
        for i, name in enumerate(chain)
    ]
    return ok(
        data={
            "skill_id": skill_id,
            "skill_name": skill["name"],
            "prerequisites": prerequisites,
        }
    )


@router.get("/skill/{skill_id}/courses")
async def skill_courses(skill_id: str):
    """技能学习课程列表（AL-M4-03，设计文档 §4.6）。

    图谱 LEARNABLE_VIA 课程按质量分降序返回（质量分来自 course_raw 评估产物），
    top 3 为学习路径推荐课程。
    """
    skill = await asyncio.to_thread(_load_skill, skill_id)
    if skill is None:
        return error(4040, "技能不存在", http_status=404)

    courses = await load_courses_for_skill(skill_id, skill["name"], top_k=None)
    return ok(
        data={
            "skill_id": skill_id,
            "skill_name": skill["name"],
            "courses": [c.model_dump() for c in courses],
        }
    )


def _load_position(id: str, user: Optional[dict] = None) -> dict | None:
    """按 ID 查询岗位节点基础属性（不含技能边），不存在返回 None。

    user/admin 可见全部岗位；匿名/guest 对 candidate/archived 岗位返回 None
    （视为不存在，避免待审核岗位外泄，见方案一）。
    """
    scope = _position_scope(user)
    status_filter = "p.status IN $public_statuses" if scope == "public" else "true"
    with neo4j_driver.session() as session:
        rec = session.run(
            f"""
            MATCH (p:Position {{id: $id}})
            WHERE {status_filter}
            RETURN p.id AS id, p.name AS name, p.required_years AS required_years,
                   p.required_education AS required_education, p.last_updated AS last_updated,
                   p.status AS status, p.freq AS freq
            """,
            id=id, public_statuses=list(_PUBLIC_POSITION_STATUSES),
        ).single()
    return dict(rec) if rec else None


@router.get("/position/{id}")
async def position_detail(
    id: str,
    user: Optional[dict] = Depends(get_optional_user),
):
    """[M4] 岗位节点详情：基础属性 + REQUIRES 技能聚合（must/nice）。

    匿名/guest 对 candidate/archived 岗位返回 404（不可见），user/admin 全量。
    """
    scope = _position_scope(user)
    cache_key = f"graph:position:{id}:{scope}"
    cached = await _cache_get(cache_key)
    if cached is not None:
        return ok(data=cached)
    position = await asyncio.to_thread(_load_position, id, user)
    if position is None:
        return error(4040, "岗位不存在", http_status=404)

    skills = await asyncio.to_thread(_query_position_skills_by_necessity, id)

    data = {
        "id": position["id"],
        "name": position.get("name", position["id"]),
        "required_years": position.get("required_years"),
        "required_education": position.get("required_education"),
        "last_updated": position.get("last_updated"),
        "status": position.get("status"),
        "must_skills": skills.get("must", []),
        "nice_skills": skills.get("nice", []),
    }
    await _cache_set(cache_key, data)
    return ok(data=data)


@router.get("/position/{id}/skills")
async def position_skills(
    id: str,
    necessity: Optional[Literal["must", "nice"]] = Query(default=None),
    user: Optional[dict] = Depends(get_optional_user),
):
    """[M4] 岗位技能列表（可按 necessity 过滤）。"""
    if await asyncio.to_thread(_load_position, id, user) is None:
        return error(4040, "岗位不存在", http_status=404)

    scope = _position_scope(user)
    status_filter = "p.status IN $public_statuses" if scope == "public" else "true"
    items = await asyncio.to_thread(
        _query_position_skills, id, necessity, status_filter)

    return ok(data={"position_id": id, "skills": items})


@router.get("/skill/{skill_id}/evidence")
async def skill_evidence(skill_id: str):
    """[M4] 技能证据列表：Skill-EVIDENCED_BY->Evidence 原始 JD。"""
    cache_key = f"graph:skill:{skill_id}:evidence"
    cached = await _cache_get(cache_key)
    if cached is not None:
        return ok(data=cached)
    skill = await asyncio.to_thread(_load_skill, skill_id)
    if skill is None:
        return error(4040, "技能不存在", http_status=404)

    evidence = await asyncio.to_thread(_query_skill_evidence, skill_id)

    data = {
        "skill_id": skill_id,
        "skill_name": skill["name"],
        "evidence": evidence,
        "evidence_count": len(evidence),
    }
    await _cache_set(cache_key, data)
    return ok(data=data)


@router.get("/skill/similar")
async def skill_similar(
    skill_id: str = Query(...),
    top_k: int = Query(default=10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    """[M4] 相似技能检索（语义相似度，设计文档 5.3 pgvector 演进落地）。

    主路径：pgvector skill_embeddings 余弦距离 Top-K（§11.4.3，IVFFLAT）。
    未回填或查询失败（表缺失/维度不匹配等）时回退内存 SBERT 全量扫描，口径一致。
    阈值 0.5，过低不返回；SBERT 不可用时返回 503（语义能力缺失，不降级为猜）。
    """
    skill = await asyncio.to_thread(_load_skill, skill_id)
    if skill is None:
        return error(4040, "技能不存在", http_status=404)

    cache_key = f"graph:skill:similar:{skill_id}:{top_k}"
    cached = await _cache_get(cache_key)
    if cached is not None:
        return ok(data=cached)

    embedder = SkillEmbedder.get()

    # pgvector 主路径：skill_embeddings 已回填则余弦距离 Top-K。
    # 查询异常（表缺失/维度不匹配）降级回退内存扫描；语义模型不可用则 503。
    try:
        target_vec_row = await db.get(SkillEmbedding, skill_id)
        if target_vec_row is not None:
            qvec = embedder.embed(skill["name"])
            rows = (
                await db.scalars(
                    select(SkillEmbedding)
                    .where(SkillEmbedding.id != skill_id)
                    .order_by(SkillEmbedding.embedding.cosine_distance(qvec))
                    .limit(200)  # 多取后按阈值过滤，保证 Top-K 质量
                )
            ).all()
            similar = [
                (r.id, r.payload.get("name", r.id), 1.0 - float(r.embedding.cosine_distance(qvec)))
                for r in rows
            ]
            similar = sorted((s for s in similar if s[2] >= 0.5), key=lambda x: x[2], reverse=True)[:top_k]
            data = {
                "skill_id": skill_id,
                "skill_name": skill["name"],
                "similar": [
                    {"skill_id": sid, "skill_name": name, "similarity": round(score, 4)}
                    for sid, name, score in similar
                ],
            }
            await _cache_set(cache_key, data)
            return ok(data=data)
    except SemanticUnavailableError:
        return error(5000, "语义模型不可用，无法计算相似技能", http_status=503)
    except Exception:
        # skill_embeddings 表缺失 / 向量维度不匹配等 → 降级回退内存扫描
        pass

    # 回退路径：skill_embeddings 未回填（表空/缺该技能），内存 SBERT 全量扫描
    all_skills = await asyncio.to_thread(_query_all_skills)
    if not all_skills:
        data = {"skill_id": skill_id, "skill_name": skill["name"], "similar": []}
        await _cache_set(cache_key, data)
        return ok(data=data)

    try:
        scores = [
            (sid, name, embedder.similarity(skill["name"], name))
            for sid, name in all_skills
            if sid != skill_id
        ]
    except SemanticUnavailableError:
        return error(5000, "语义模型不可用，无法计算相似技能", http_status=503)

    similar = sorted(
        (s for s in scores if s[2] >= 0.5),
        key=lambda x: x[2],
        reverse=True,
    )[:top_k]
    data = {
        "skill_id": skill_id,
        "skill_name": skill["name"],
        "similar": [
            {"skill_id": sid, "skill_name": name, "similarity": round(score, 4)}
            for sid, name, score in similar
        ],
    }
    await _cache_set(cache_key, data)
    return ok(data=data)


@router.get("/skill/{skill_id}")
async def skill_detail(
    skill_id: str,
    user: Optional[dict] = Depends(get_optional_user),
):
    """[M4] 技能节点详情：基础属性 + 关联计数（岗位/证据/课程）。

    定义在 /skill/similar 之后，避免静态段 similar 被 {skill_id} 参数路径截胡。
    """
    scope = _position_scope(user)
    cache_key = f"graph:skill:{skill_id}:{scope}"
    cached = await _cache_get(cache_key)
    if cached is not None:
        return ok(data=cached)
    skill = await asyncio.to_thread(_load_skill, skill_id)
    if skill is None:
        return error(4040, "技能不存在", http_status=404)

    status_filter = "p.status IN $public_statuses" if scope == "public" else "true"
    counts = await asyncio.to_thread(_query_skill_counts, skill_id, status_filter)

    courses = await load_courses_for_skill(skill_id, skill["name"], top_k=None)
    data = {
        "id": skill_id,
        "name": skill["name"],
        "positions_count": counts.get("positions_count", 0),
        "evidence_count": counts.get("evidence_count", 0),
        "courses_count": len(courses),
    }
    await _cache_set(cache_key, data)
    return ok(data=data)


# 图算法默认参数（import 时从 configs/graph_algo.yaml 读取——Optuna 最优 γ/min_weight
# 接入 API 运行时路径，与 sync_communities 索引同口径；修改配置后重启生效）
_GRAPH_ALGO_DEFAULTS = load_graph_algo_config()


@router.get("/algorithms/pagerank")
async def graph_pagerank(
    top_n: int = Query(default=20, ge=1, le=100),
    min_weight: float = Query(default=_GRAPH_ALGO_DEFAULTS["min_weight"], ge=1.0),
):
    """PageRank 技能重要性 Top-N（设计文档 7.1 图算法应用）。

    技能网络 = 岗位共现（两技能被同一岗位 REQUIRES 即连边），纯 Python
    幂迭代（Neo4j 社区版无 GDS 插件）。min_weight 默认取 configs/graph_algo.yaml
    （调优值 2.5021，与 skill-clusters 端点取数口径一致）。30s Redis TTL 缓存。
    """
    from app.services.graph_algorithms.network import load_skill_cooccurrence
    from app.services.graph_algorithms.pagerank import pagerank

    cache_key = f"graph:algo:pagerank:{top_n}:{min_weight}"
    cached = await redis_client.get(cache_key)
    if cached is not None:
        return ok(data=json.loads(cached))

    def _compute():
        # 同步 Neo4j 会话 + 幂迭代为 CPU/IO 密集，放线程池避免阻塞事件循环
        with neo4j_driver.session() as session:
            graph, name_map = load_skill_cooccurrence(session, min_weight=min_weight)
        scores = pagerank(graph)
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
        return name_map, ranked

    name_map, ranked = await asyncio.to_thread(_compute)
    skills = [
        {"id": sid, "name": name_map.get(sid, sid), "score": round(score, 6)}
        for sid, score in ranked
    ]
    data = {"skills": skills, "top_n": len(skills)}
    await redis_client.set(cache_key, json.dumps(data), ex=30)
    return ok(data=data)


@router.get("/algorithms/skill-clusters")
async def graph_skill_clusters(
    min_size: int = Query(default=2, ge=1, le=100),
    resolution: float = Query(default=_GRAPH_ALGO_DEFAULTS["resolution"], ge=0.1, le=5.0),
    level: Optional[int] = Query(default=None, ge=0, le=31),
):
    """Louvain/Leiden 技能簇（设计文档 7.1 图算法应用，技术栈视图支撑）。

    同一簇内技能常共现于同一批岗位（如大数据栈 / AI 栈）。聚类算法由
    configs/graph_algo.yaml 的 algorithm 字段决定（louvain 默认，阶段二
    Leiden 条件替换：同签名 leiden()，seed=0 确定性；依赖缺失自动回退
    louvain 并告警）。min_size 过滤过小簇；resolution 为分辨率参数 γ
    （图算法优化方案阶段一：>1 细簇 / <1 粗簇 / 1.0 等价标准 Louvain，
    默认值取 configs/graph_algo.yaml）。

    阶段三层次化提取：level 指定 dendrogram 层级（0 = 最细，逐层变粗，
    默认 None = 最优层，与 louvain() 输出一致）；响应附 levels 元数据
    （level/cluster_count/modularity，供前端层级导航；Leiden 算法不支持
    层级，level 参数忽略且 levels 为 null）。

    图算法优化方案 §4：输出经规则优先后处理（孤立簇剔除/过小簇合并/
    规则标签）+ LLM 兜底（仅 needs_llm 簇调用，失败降级规则标签），
    响应附 needs_llm/triggers/llm 字段。30s Redis TTL 缓存（键含
    algorithm + resolution + level，防新旧参数/算法/层级串缓存）。
    """
    from app.services.extraction.dictionary import skill_category
    from app.services.graph_algorithms.cluster_llm import ClusterLLMClassifier
    from app.services.graph_algorithms.network import load_skill_cooccurrence
    from app.services.graph_algorithms.postprocess import ClusterPostProcessor

    algorithm = load_graph_algo_config()["algorithm"]
    cache_key = f"graph:algo:clusters:{algorithm}:{min_size}:{resolution}:{level}"
    cached = await redis_client.get(cache_key)
    if cached is not None:
        return ok(data=json.loads(cached))

    def _run_clustering(graph):
        """按配置选择聚类算法：leiden 优先（依赖缺失回退 louvain 并告警）。"""
        if algorithm == "leiden":
            try:
                from app.services.graph_algorithms.leiden import leiden

                return leiden(graph, resolution=resolution), None
            except ImportError:
                logger.warning(
                    "leiden 依赖（igraph/leidenalg）不可用，回退 louvain（algorithm=%s）", algorithm
                )
        from app.services.graph_algorithms.louvain import louvain_hierarchical

        hier = louvain_hierarchical(graph, resolution=resolution)
        if level is None:
            return hier["membership"], hier
        # 指定层级：不存在（越界）时回退最粗层
        by_level = {lv["level"]: lv["membership"] for lv in hier["levels"]}
        membership = by_level.get(level, hier["levels"][-1]["membership"])
        return membership, hier

    def _compute():
        # 同步 Neo4j 会话 + 聚类 + 后处理 + LLM 兜底为 CPU/IO 密集，
        # 放线程池避免阻塞事件循环。
        # min_weight=2.0（默认）：P0 改造后权重=必要性组合因子×共现数，
        # 过滤 must-nice 低频与 nice-nice 弱边，聚类簇内同质性最佳
        with neo4j_driver.session() as session:
            graph, name_map = load_skill_cooccurrence(session, min_weight=_GRAPH_ALGO_DEFAULTS["min_weight"])
        clusters, hier = _run_clustering(graph)

        # 规则优先后处理 + LLM 兜底触发标记（图算法优化方案 §4.1-4.2）
        categories = {sid: skill_category(name) for sid, name in name_map.items()}
        processed = ClusterPostProcessor().process(clusters, graph, name_map, categories)

        # LLM 兜底（§4.3-4.4）：仅对 needs_llm 且非孤立的簇调用，失败降级规则标签
        # （ClusterLLMClassifier.classify 内部已捕获 LLM 异常，不阻塞 API）
        classifier = ClusterLLMClassifier()
        for c in processed["clusters"]:
            if c["needs_llm"] and not c["orphan"]:
                skills = [name_map.get(sid, sid) for sid in c["skills"]]
                decision = classifier.classify(skills, c["triggers"], c["label"])
                c["llm"] = {
                    "coherent": decision.coherent,
                    "cluster_name": decision.cluster_name,
                    "rationale": decision.rationale,
                    "splits": decision.splits,
                }

        items = []
        for c in processed["clusters"]:
            if c["orphan"]:
                continue
            if len(c["skills"]) < min_size:
                continue
            items.append({
                "id": c["cluster_id"],
                "size": len(c["skills"]),
                "label": c["label"],
                "needs_llm": c["needs_llm"],
                "triggers": c["triggers"],
                "llm": c.get("llm"),
                "skills": [{"id": sid, "name": name_map.get(sid, sid)} for sid in c["skills"]],
            })
        # 层级元数据：每层经后处理（无 LLM）+ min_size 过滤后的实际簇数，
        # 与对应 level 请求的结果同口径（细层单点簇会被过小簇合并，须如实反映）；
        # modularity 统一用标准 Q（γ=1.0），与评估报告/验收口径一致
        level_counts = None
        if hier is not None:
            from app.services.graph_algorithms.louvain import modularity

            level_counts = []
            for lv in hier["levels"]:
                lv_processed = ClusterPostProcessor().process(lv["membership"], graph, name_map, categories)
                n = sum(
                    1 for c in lv_processed["clusters"]
                    if not c["orphan"] and len(c["skills"]) >= min_size
                )
                level_counts.append({
                    "level": lv["level"],
                    "cluster_count": n,
                    "modularity": round(modularity(graph, lv["membership"], 1.0), 6),
                })
        return items, level_counts

    items, level_counts = await asyncio.to_thread(_compute)
    data = {"clusters": items, "cluster_count": len(items)}
    # 阶段三层级元数据（Leiden 不支持层级时为 null，前端隐藏层级导航）
    data["levels"] = level_counts
    await redis_client.set(cache_key, json.dumps(data), ex=30)
    return ok(data=data)


@router.get("/algorithms/community-tree")
async def graph_community_tree():
    """社区层级树（图算法优化方案阶段三：层次化提取，dendrogram 可视化）。

    读取 scripts/sync_communities.py 写入的 Neo4j Community 节点：
    - 节点：`(:Community {id: comm_{level}_{cluster}, name, level, modularity, cluster_count})`
    - 边：`(:Skill)-[:BELONGS_TO_COMMUNITY]->(:Community)` + `(:Community)-[:NESTED_IN]->(:Community)`

    响应为树结构（顶层 = 最高层社区，children 按 NESTED_IN 展开），
    供 ECharts tree 系列 dendrogram 渲染。未同步（无 Community 节点）时
    返回空树（前端提示先运行 scripts/sync_communities.py）。
    30s Redis TTL 缓存。
    """
    cache_key = "graph:algo:community-tree"
    cached = await redis_client.get(cache_key)
    if cached is not None:
        return ok(data=json.loads(cached))

    def _compute():
        with neo4j_driver.session() as session:
            rows = session.run(
                """
                MATCH (s:Skill)-[r:BELONGS_TO_COMMUNITY]->(c:Community)
                RETURN c.id AS cid, c.level AS level, c.name AS name,
                       c.cluster_count AS cluster_count, c.modularity AS modularity,
                       collect(s.name)[..5] AS top_skills
                """
            )
            nodes: dict[str, dict] = {}
            for rec in rows:
                cid = rec["cid"]
                nodes[cid] = {
                    "id": cid,
                    "name": rec.get("name") or cid,
                    "level": rec["level"],
                    "cluster_count": rec["cluster_count"] or 0,
                    "modularity": rec["modularity"] or 0.0,
                    "top_skills": rec.get("top_skills") or [],
                    "children": [],
                }
            parent_rows = session.run(
                "MATCH (c1:Community)-[:NESTED_IN]->(c2:Community) RETURN c1.id AS child, c2.id AS parent"
            )
            children_of: dict[str, list[str]] = {}
            for rec in parent_rows:
                children_of.setdefault(rec["parent"], []).append(rec["child"])
        # 组装树：children 按 NESTED_IN 展开；根 = 无父节点（最高层社区）
        for cid, child_ids in children_of.items():
            if cid in nodes:
                nodes[cid]["children"] = [nodes[c] for c in child_ids if c in nodes]
        child_set = {c for children in children_of.values() for c in children}
        roots = [n for n in nodes.values() if n["id"] not in child_set]
        levels = sorted({n["level"] for n in nodes.values()})
        return {"tree": roots, "levels": levels}

    data = await asyncio.to_thread(_compute)
    await redis_client.set(cache_key, json.dumps(data), ex=30)
    return ok(data=data)


@router.get("/algorithms/shortest-path")
async def graph_shortest_path(
    from_skill: str = Query(..., alias="from"),
    to_skill: str = Query(..., alias="to"),
    user: Optional[dict] = Depends(get_optional_user),
):
    """技能最短路径（设计文档 7.1 图算法应用，学习路径先修排序）。

    shortestPath((:Skill)-[*..6]-(:Skill))，路径可能经过 Position 节点
    （岗位共现边），节点序列按 type 区分。不存在可达路径返回 404。
    匿名/guest 路径经过的 Position 节点仅限公开态（candidate 不外宣）。
    """

    scope = _position_scope(user)
    statuses = list(_PUBLIC_POSITION_STATUSES) if scope == "public" else None
    path = await asyncio.to_thread(
        _query_shortest_path, from_skill, to_skill, statuses)
    if path is None:
        return error(4040, "两技能间不存在 ≤6 跳的可达路径", http_status=404)
    return ok(data={"from": from_skill, "to": to_skill, "path": path})


@router.get("/view/{view_type}")
async def graph_view(
    view_type: Literal["panorama", "techStack", "level", "positionCenter"],
    limit: int = Query(default=100, ge=1, le=600),
    user: Optional[dict] = Depends(get_optional_user),
):
    """[M4] 视图切换（匿名可读，后端过滤，同构于全景图）。

    四种视图统一返回 {view_type, nodes, edges, stats}：
    - panorama / positionCenter: 岗位中心展开（岗位→技能）
    - techStack: 技能为中心，边反向为技能→岗位，节点按技能频次排序
    - level: 岗位中心展开 + 按熟练度级别过滤（只保留明确 level 的边）
    匿名/guest 仅返回 emerging/stable/declining 岗位（candidate 待审核不外宣）。
    """
    scope = _position_scope(user)
    cache_key = f"graph:view:{view_type}:{limit}:{scope}"
    cached = await redis_client.get(cache_key)
    if cached is not None:
        return ok(data=json.loads(cached))

    status_filter = "p.status IN $public_statuses" if scope == "public" else "true"

    if view_type == "techStack":
        rows = await asyncio.to_thread(
            _query_view_techstack, limit, status_filter)
        nodes: dict[str, dict] = {}
        edges: list[dict] = []
        for record in rows:
            s_id, p_id = record["sid"], record["pid"]
            nodes.setdefault(s_id, {"id": s_id, "name": record.get("sname", s_id), "type": "skill"})
            nodes.setdefault(p_id, {
                "id": p_id,
                "name": record.get("pname", p_id),
                "type": "position",
                "status": record.get("pstatus") or "candidate",
            })
            edges.append({
                "source": s_id,
                "target": p_id,
                "weight": record["r"].get("weight", 0.0),
                "necessity": record["r"].get("necessity", "must"),
                "level": record["r"].get("level", "中级"),
            })
        data = {
            "view_type": view_type,
            "nodes": list(nodes.values()),
            "edges": edges,
            "stats": {"nodes": len(nodes), "edges": len(edges),
                      **await asyncio.to_thread(_query_graph_counts)},
        }
    else:
        rows = await asyncio.to_thread(
            _query_view_main, limit, status_filter)
        nodes = {}
        edges = []
        for record in rows:
            p, s, r = record["p"], record["s"], record["r"]
            p_id, s_id = p.get("id", ""), s.get("id", "")
            nodes.setdefault(p_id, {
                "id": p_id,
                "name": p.get("name", p_id),
                "type": "position",
                "status": p.get("status", "candidate"),
            })
            nodes.setdefault(s_id, {"id": s_id, "name": s.get("name", s_id), "type": "skill"})
            edges.append({
                "source": p_id,
                "target": s_id,
                "weight": r.get("weight", 0.0),
                "necessity": r.get("necessity", "must"),
                "level": r.get("level", "中级"),
            })
        data = {
            "view_type": view_type,
            "nodes": list(nodes.values()),
            "edges": edges,
            "stats": {"nodes": len(nodes), "edges": len(edges),
                      **await asyncio.to_thread(_query_graph_counts)},
        }

    await redis_client.set(cache_key, json.dumps(data), ex=30)
    return ok(data=data)
