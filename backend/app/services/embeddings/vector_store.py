"""pgvector 向量存取与消费辅助（设计文档 §11.4.3 三表）。

- load_*：将各 embedding 表按业务键映射为 {key: vector}，供消费点使用
  （skill/similar 端点、dedup_simhash 语义辅助、engine._project_score 项目比对）
"""

from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business import JdEmbedding, ProjectEmbedding
from app.services.matching.semantic import cosine_similarity  # noqa: F401  （供调用方直接使用）


class PgvectorUnavailableError(Exception):
    """pgvector 向量查询失败（表缺失/维度不匹配/超时等，契约错误码 5002）。

    消费点按需降级（skill/similar → 内存扫描、项目比对 → 文本相似度）；
    未降级路径由 main.py 全局处理器兜底发射 5002。
    """


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
    try:
        rows = (await db.scalars(stmt)).all()
    except SQLAlchemyError as e:
        raise PgvectorUnavailableError(f"project_embeddings 查询失败: {e}") from e
    return {r.payload.get("text", ""): r.embedding for r in rows if r.payload.get("text")}


async def load_jd_vectors_by_ids(db: AsyncSession, jd_ids: list[str]) -> dict[str, list[float]]:
    """按 jd_id 集合加载 jd_embeddings（08-14 审查：dedup 语义校验按需加载，
    此前全量向量入内存；pairs 通常远少于全量记录数）。"""
    if not jd_ids:
        return {}
    try:
        rows = (await db.scalars(
            select(JdEmbedding).where(JdEmbedding.jd_id.in_(jd_ids))
        )).all()
    except SQLAlchemyError as e:
        raise PgvectorUnavailableError(f"jd_embeddings 查询失败: {e}") from e
    return {r.jd_id: r.embedding for r in rows}
