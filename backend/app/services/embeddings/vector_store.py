"""pgvector 向量存取与消费辅助（设计文档 §11.4.3 三表）。

- load_*：将各 embedding 表按业务键映射为 {key: vector}，供消费点使用
  （skill/similar 端点、dedup_simhash 语义辅助、engine._project_score 项目比对）
"""

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business import JdEmbedding, ProjectEmbedding, SkillEmbedding
from app.services.matching.semantic import cosine_similarity  # noqa: F401  （供调用方直接使用）


async def load_skill_embeddings(db: AsyncSession) -> dict[str, list[float]]:
    """skill_embeddings → {skill_id: vector}。"""
    rows = (await db.scalars(select(SkillEmbedding))).all()
    return {r.id: r.embedding for r in rows}


async def load_project_vectors(
    db: AsyncSession, resume_id: Optional[str] = None
) -> dict[str, list[float]]:
    """project_embeddings → {项目文本: vector}（按 resume_id 过滤）。

    项目文本与 engine._project_score 的拼接口径一致（name + "：" + description），
    缺失回填时返回空 dict，调用方回退 SBERT 文本相似度。
    """
    stmt = select(ProjectEmbedding)
    if resume_id:
        # 独立 resume_id 列（含 UniqueConstraint(resume_id, project_index) 索引），
        # 优于 JSONB 路径过滤 payload["resume_id"]
        stmt = stmt.where(ProjectEmbedding.resume_id == resume_id)
    rows = (await db.scalars(stmt)).all()
    return {r.payload.get("text", ""): r.embedding for r in rows if r.payload.get("text")}


async def load_jd_vectors(db: AsyncSession) -> dict[str, list[float]]:
    """jd_embeddings → {jd_id: vector}。"""
    rows = (await db.scalars(select(JdEmbedding))).all()
    return {r.payload.get("jd_id"): r.embedding for r in rows if r.payload.get("jd_id")}
