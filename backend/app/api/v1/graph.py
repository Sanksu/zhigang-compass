"""图谱路由：全景、视图、搜索、技能反向查询。"""

from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.core.database import get_neo4j
from app.schemas.common import ok, error

router = APIRouter()


@router.get("/panorama")
async def panorama(
    limit: int = Query(default=100, ge=1, le=600),
    min_weight: float = Query(default=0.3, ge=0.0, le=1.0),
    focus: Optional[str] = Query(default=None),
):
    """图谱全景视图（30s Redis TTL 缓存，见设计文档 10.3）。"""
    # TODO: Neo4j Cypher 查询 Top-N 高频岗位 + 关联技能
    return error(501, "待实现 — Neo4j panorama 查询")


@router.get("/skill/{skill_id}/positions")
async def skill_positions(skill_id: str):
    """技能节点反向查询：返回关联的岗位列表 + necessity + weight + level。"""
    # TODO: MATCH (p:Position)-[r:REQUIRES]->(s:Skill {id:$skill_id}) RETURN ...
    return error(501, "待实现")


@router.get("/search")
async def fulltext_search(
    q: str = Query(..., min_length=1),
    limit: int = Query(default=20, ge=1, le=100),
):
    """Neo4j 全文检索（cjk 分词器）。"""
    return error(501, "待实现")
