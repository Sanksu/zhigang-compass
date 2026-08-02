"""图谱路由：全景、技能反向查询、全文检索、先修链、学习课程。"""

import json
from typing import Optional

from fastapi import APIRouter, Query

from app.core.database import neo4j_driver, redis_client
from app.schemas.common import error, ok
from app.services.learning_path.courses import load_courses_for_skill
from app.services.learning_path.prerequisites import prerequisite_chain

router = APIRouter()

# 全景查询缓存 TTL（设计文档 10.3：panorama 短 TTL 30s）
PANORAMA_CACHE_TTL = 30


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
    return ok(data={"skill_id": skill_id, "positions": positions})


@router.get("/search")
async def fulltext_search(
    q: str = Query(..., min_length=1),
    type_: str = Query(default="position", alias="type", enum=["position", "skill", "evidence"]),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
):
    """Neo4j 全文检索（cjk 分词器，设计文档 5.4）。

    position/skill 走全文索引；evidence 无全文索引（schema 未建），
    用 CONTAINS 属性过滤兜底。
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
            result = session.run(
                """
                MATCH (e:Evidence)
                WHERE e.name CONTAINS $q OR e.source CONTAINS $q
                RETURN e.id AS id, e.name AS name, 0.0 AS score
                ORDER BY id SKIP $offset LIMIT $size
                """,
                q=q, offset=offset, size=size,
            )
            total_row = session.run(
                """
                MATCH (e:Evidence)
                WHERE e.name CONTAINS $q OR e.source CONTAINS $q
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
