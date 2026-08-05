"""图谱路由：全景、技能反向查询、全文检索、先修链、学习课程。"""

import json
from typing import Literal, Optional

from fastapi import APIRouter, Query

from app.core.database import neo4j_driver, redis_client
from app.schemas.common import error, ok
from app.services.learning_path.courses import load_courses_for_skill
from app.services.learning_path.prerequisites import prerequisite_chain
from app.services.matching.semantic import SkillEmbedder, SemanticUnavailableError

router = APIRouter()

# 全景查询缓存 TTL（设计文档 10.3：panorama 短 TTL 30s）
PANORAMA_CACHE_TTL = 30

# 节点详情缓存 TTL（设计文档 §11.3.5：position:{id} 5min，skill 同档）
_NODE_CACHE_TTL = 300


async def _cache_get(key: str):
    """Redis 缓存读取（JSON 反序列化），未命中返回 None。"""
    cached = await redis_client.get(key)
    return json.loads(cached) if cached else None


async def _cache_set(key: str, data, ttl: int = _NODE_CACHE_TTL) -> None:
    """Redis 缓存写入（JSON 序列化）。"""
    await redis_client.set(key, json.dumps(data), ex=ttl)


@router.get("/panorama")
async def panorama(
    limit: int = Query(default=100, ge=1, le=600),
    min_weight: float = Query(default=0.3, ge=0.0, le=1.0),
    focus: Optional[str] = Query(default=None),
):
    """图谱全景视图（30s Redis TTL 缓存，见设计文档 10.3）。

    focus 缺省时返回 Top-N 高频岗位 + 关联技能；指定 focus 时以该岗位为中心展开。
    """
    cache_key = f"graph:panorama:{limit}:{min_weight}:{focus or 'all'}"
    cached = await redis_client.get(cache_key)
    if cached is not None:
        return ok(data=json.loads(cached))

    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    with neo4j_driver.session() as session:
        if focus:
            rows = session.run(
                """
                MATCH (p:Position {id: $focus})-[r:REQUIRES]->(s:Skill)
                WHERE r.weight >= $min_weight
                RETURN p, s, r
                """,
                focus=focus, min_weight=min_weight,
            )
        else:
            # 先按岗位热度取 Top-N 岗位，再展开其边（limit 语义为岗位数，避免低频岗位被边数截断）
            rows = session.run(
                """
                MATCH (p:Position)
                WITH p ORDER BY coalesce(p.freq, 0) DESC, p.name LIMIT $limit
                MATCH (p)-[r:REQUIRES]->(s:Skill)
                WHERE r.weight >= $min_weight
                RETURN p, s, r
                """,
                limit=limit, min_weight=min_weight,
            )

        for record in rows:
            p, s, r = record["p"], record["s"], record["r"]
            p_id = p.get("id", "")
            s_id = s.get("id", "")
            nodes.setdefault(p_id, {"id": p_id, "name": p.get("name", p_id), "type": "position"})
            nodes.setdefault(s_id, {"id": s_id, "name": s.get("name", s_id), "type": "skill"})
            edges.append({
                "source": p_id,
                "target": s_id,
                "weight": r.get("weight", 0.0),
                "necessity": r.get("necessity", "must"),
                "level": r.get("level", "中级"),
            })

    data = {
        "nodes": list(nodes.values()),
        "edges": edges,
        "stats": {"nodes": len(nodes), "edges": len(edges)},
    }
    await redis_client.set(cache_key, json.dumps(data), ex=PANORAMA_CACHE_TTL)
    return ok(data=data)


@router.get("/skill/{skill_id}/positions")
async def skill_positions(skill_id: str):
    """技能节点反向查询：返回关联的岗位列表 + necessity + weight + level。"""
    cache_key = f"graph:skill:{skill_id}:positions"
    cached = await _cache_get(cache_key)
    if cached is not None:
        return ok(data=cached)
    with neo4j_driver.session() as session:
        rows = session.run(
            """
            MATCH (p:Position)-[r:REQUIRES]->(s:Skill {id: $skill_id})
            RETURN p.id AS position_id, p.name AS position_name,
                   r.necessity AS necessity, r.weight AS weight, r.level AS level
            ORDER BY r.weight DESC
            """,
            skill_id=skill_id,
        )
        positions = [
            {
                "position_id": rec["position_id"],
                "position_name": rec.get("position_name", rec["position_id"]),
                "necessity": rec.get("necessity", "must"),
                "weight": rec.get("weight", 0.0),
                "level": rec.get("level", "中级"),
            }
            for rec in rows
        ]
    data = {"skill_id": skill_id, "positions": positions}
    await _cache_set(cache_key, data)
    return ok(data=data)


@router.get("/search")
async def fulltext_search(
    q: str = Query(..., min_length=1),
    type_: str = Query(default="position", alias="type", enum=["position", "skill", "evidence"]),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
):
    """Neo4j 全文检索（cjk 分词器，设计文档 5.4）。

    position/skill 走全文索引；evidence 走 evidence_search 全文索引
    （M17 新增，索引缺失时降级 CONTAINS）。
    """
    offset = (page - 1) * size
    items: list[dict] = []
    total = 0

    with neo4j_driver.session() as session:
        if type_ in ("position", "skill"):
            index = "position_search" if type_ == "position" else "skill_search"
            result = session.run(
                f"""
                CALL db.index.fulltext.queryNodes('{index}', $q) YIELD node, score
                RETURN node.id AS id, node.name AS name, score
                ORDER BY score DESC SKIP $offset LIMIT $size
                """,
                q=q, offset=offset, size=size,
            )
            total_row = session.run(
                f"CALL db.index.fulltext.queryNodes('{index}', $q) YIELD node RETURN count(node) AS c",
                q=q,
            ).single()
            total = total_row["c"] if total_row else 0
        else:
            # Evidence 全文索引：schema.cypher 建 evidence_search
            # （ON source, raw_text，cjk 分词）。Evidence 无 name 属性，返回 source
            # 作为展示名；索引缺失（旧库未重跑 init_neo4j）时降级 CONTAINS 兜底。
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

    先修链来自人工维护字典 configs/skill_prerequisites.yaml（图谱无
    PREREQUISITE_OF 边），返回拓扑序（先修在前），并富化图谱技能 ID。
    """
    skill = _load_skill(skill_id)
    if skill is None:
        return error(404, "技能不存在")

    chain = prerequisite_chain(skill["name"])
    id_by_name: dict[str, str] = {}
    if chain:
        with neo4j_driver.session() as session:
            rows = session.run(
                "MATCH (s:Skill) WHERE s.name IN $names RETURN s.name AS name, s.id AS id",
                names=chain,
            )
            id_by_name = {rec["name"]: rec["id"] for rec in rows}
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
    skill = _load_skill(skill_id)
    if skill is None:
        return error(404, "技能不存在")

    courses = await load_courses_for_skill(skill_id, skill["name"], top_k=None)
    return ok(
        data={
            "skill_id": skill_id,
            "skill_name": skill["name"],
            "courses": [c.model_dump() for c in courses],
        }
    )


def _load_position(id: str) -> dict | None:
    """按 ID 查询岗位节点基础属性（不含技能边），不存在返回 None。"""
    with neo4j_driver.session() as session:
        rec = session.run(
            """
            MATCH (p:Position {id: $id})
            RETURN p.id AS id, p.name AS name, p.required_years AS required_years,
                   p.required_education AS required_education, p.last_updated AS last_updated,
                   p.status AS status, p.freq AS freq
            """,
            id=id,
        ).single()
    return dict(rec) if rec else None


@router.get("/position/{id}")
async def position_detail(id: str):
    """[M4] 岗位节点详情：基础属性 + REQUIRES 技能聚合（must/nice）。"""
    cache_key = f"graph:position:{id}"
    cached = await _cache_get(cache_key)
    if cached is not None:
        return ok(data=cached)
    position = _load_position(id)
    if position is None:
        return error(404, "岗位不存在")

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
):
    """[M4] 岗位技能列表（可按 necessity 过滤）。"""
    if _load_position(id) is None:
        return error(404, "岗位不存在")

    query = """
        MATCH (p:Position {id: $id})-[r:REQUIRES]->(s:Skill)
        WHERE ($necessity IS NULL OR r.necessity = $necessity)
        RETURN s.id AS skill_id, s.name AS skill_name,
               r.necessity AS necessity, r.weight AS weight,
               r.level AS level, r.source_count AS source_count
        ORDER BY r.weight DESC
    """
    with neo4j_driver.session() as session:
        rows = session.run(query, id=id, necessity=necessity)
        items = [
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

    return ok(data={"position_id": id, "skills": items})


@router.get("/skill/{skill_id}/evidence")
async def skill_evidence(skill_id: str):
    """[M4] 技能证据列表：Skill-MENTIONED_IN->Evidence 原始 JD。"""
    cache_key = f"graph:skill:{skill_id}:evidence"
    cached = await _cache_get(cache_key)
    if cached is not None:
        return ok(data=cached)
    skill = _load_skill(skill_id)
    if skill is None:
        return error(404, "技能不存在")

    with neo4j_driver.session() as session:
        rows = session.run(
            """
            MATCH (s:Skill {id: $skill_id})-[:MENTIONED_IN]->(e:Evidence)
            RETURN e.id AS id, e.source AS source, e.source_url AS source_url,
                   e.crawled_at AS crawled_at
            ORDER BY e.crawled_at DESC
            """,
            skill_id=skill_id,
        )
        evidence = [
            {
                "id": rec["id"],
                "source": rec.get("source", ""),
                "source_url": rec.get("source_url", ""),
                "crawled_at": rec.get("crawled_at"),
            }
            for rec in rows
        ]

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
):
    """[M4] 相似技能检索（语义相似度，设计文档 5.3 预留 pgvector 演进）。

    用 SkillEmbedder 对全部图谱技能名计算余弦相似度取 Top-K（阈值 0.5，
    过低不返回）。SBERT 不可用时返回 503（语义能力缺失，不降级为猜）。
    """
    skill = _load_skill(skill_id)
    if skill is None:
        return error(404, "技能不存在")

    cache_key = f"graph:skill:similar:{skill_id}:{top_k}"
    cached = await _cache_get(cache_key)
    if cached is not None:
        return ok(data=cached)

    with neo4j_driver.session() as session:
        rows = session.run(
            "MATCH (s:Skill) RETURN s.id AS id, s.name AS name"
        )
        all_skills = [(rec["id"], rec.get("name", rec["id"])) for rec in rows]
    if not all_skills:
        data = {"skill_id": skill_id, "skill_name": skill["name"], "similar": []}
        await _cache_set(cache_key, data)
        return ok(data=data)

    embedder = SkillEmbedder.get()
    try:
        scores = [
            (sid, name, embedder.similarity(skill["name"], name))
            for sid, name in all_skills
            if sid != skill_id
        ]
    except SemanticUnavailableError:
        return error(503, "语义模型不可用，无法计算相似技能")

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
async def skill_detail(skill_id: str):
    """[M4] 技能节点详情：基础属性 + 关联计数（岗位/证据/课程）。

    定义在 /skill/similar 之后，避免静态段 similar 被 {skill_id} 参数路径截胡。
    """
    cache_key = f"graph:skill:{skill_id}"
    cached = await _cache_get(cache_key)
    if cached is not None:
        return ok(data=cached)
    skill = _load_skill(skill_id)
    if skill is None:
        return error(404, "技能不存在")

    with neo4j_driver.session() as session:
        rec = session.run(
            """
            MATCH (s:Skill {id: $skill_id})
            OPTIONAL MATCH (p:Position)-[r:REQUIRES]->(s)
            OPTIONAL MATCH (s)-[:MENTIONED_IN]->(e:Evidence)
            RETURN count(DISTINCT p) AS positions_count, count(DISTINCT e) AS evidence_count
            """,
            skill_id=skill_id,
        ).single()
        counts = dict(rec) if rec else {}

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


@router.get("/view/{view_type}")
async def graph_view(
    view_type: Literal["panorama", "techStack", "level", "positionCenter"],
    limit: int = Query(default=100, ge=1, le=600),
):
    """[M4] 视图切换（后端过滤，同构于全景图）。

    四种视图统一返回 {view_type, nodes, edges, stats}：
    - panorama / positionCenter: 岗位中心展开（岗位→技能）
    - techStack: 技能为中心，边反向为技能→岗位，节点按技能频次排序
    - level: 岗位中心展开 + 按熟练度级别过滤（只保留明确 level 的边）
    """
    cache_key = f"graph:view:{view_type}:{limit}"
    cached = await redis_client.get(cache_key)
    if cached is not None:
        return ok(data=json.loads(cached))

    if view_type == "techStack":
        with neo4j_driver.session() as session:
            rows = list(session.run(
                """
                MATCH (s:Skill)<-[r:REQUIRES]-(p:Position)
                WITH s, count(p) AS heat
                ORDER BY heat DESC LIMIT $limit
                MATCH (s)<-[r:REQUIRES]-(p:Position)
                RETURN s.id AS sid, s.name AS sname, p.id AS pid, p.name AS pname, r
                """,
                limit=limit,
            ))
        nodes: dict[str, dict] = {}
        edges: list[dict] = []
        for record in rows:
            s_id, p_id = record["sid"], record["pid"]
            nodes.setdefault(s_id, {"id": s_id, "name": record.get("sname", s_id), "type": "skill"})
            nodes.setdefault(p_id, {"id": p_id, "name": record.get("pname", p_id), "type": "position"})
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
            "stats": {"nodes": len(nodes), "edges": len(edges)},
        }
    else:
        with neo4j_driver.session() as session:
            rows = list(session.run(
                """
                MATCH (p:Position)
                WITH p ORDER BY coalesce(p.freq, 0) DESC, p.name LIMIT $limit
                MATCH (p)-[r:REQUIRES]->(s:Skill)
                RETURN p, s, r
                """,
                limit=limit,
            ))
        nodes = {}
        edges = []
        for record in rows:
            p, s, r = record["p"], record["s"], record["r"]
            p_id, s_id = p.get("id", ""), s.get("id", "")
            nodes.setdefault(p_id, {"id": p_id, "name": p.get("name", p_id), "type": "position"})
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
            "stats": {"nodes": len(nodes), "edges": len(edges)},
        }

    await redis_client.set(cache_key, json.dumps(data), ex=30)
    return ok(data=data)
