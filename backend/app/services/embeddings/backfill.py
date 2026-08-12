"""pgvector 三表向量回填（设计文档 §11.4.3）。

三个来源：
- skill_embeddings：Neo4j 全量 Skill 名
- jd_embeddings：jd_raw 已入库记录（title+company+location 语义指纹）
- project_embeddings：resume_cache 已解析简历的项目文本

幂等 upsert（按业务键更新向量，不存在则插入）；模型不可用时抛
SemanticUnavailableError，由调用方决定跳过（不阻塞 ETL 主线）。
"""

import asyncio

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import neo4j_driver
from app.models.business import JdEmbedding, ProjectEmbedding, ResumeCache, SkillEmbedding
from app.models.raw import JDRaw
from app.services.matching.semantic import SkillEmbedder


def _fetch_skill_rows() -> list[tuple[str, str]]:
    """同步 Neo4j 读取 Skill 全量（线程池调用）。"""
    with neo4j_driver.session() as session:
        rows = session.run("MATCH (s:Skill) RETURN s.id AS id, s.name AS name").data()
    return [(r["id"], r.get("name") or r["id"]) for r in rows]


def _embed_all(embedder, texts: list[str]) -> list[list[float]]:
    """批量预计算文本向量（SBERT 推理放线程池，避免阻塞事件循环）。"""
    embedder.warm(texts)
    return [embedder.embed(t) for t in texts]


def _project_text(name: str, description: str) -> str:
    """项目向量文本口径：与 engine._project_score 拼接一致（name + 描述）。"""
    if not name and not description:
        return ""
    return name + (f"：{description}" if description else "")


def _jd_text(snapshot: dict) -> str:
    """JD 语义指纹文本：title + company + location（与 SimHash 字段口径相近）。"""
    return " ".join(
        filter(None, [
            snapshot.get("title", ""),
            snapshot.get("company", ""),
            snapshot.get("location", ""),
        ])
    )


async def backfill_skill_embeddings(db: AsyncSession, embedder) -> dict:
    """Neo4j Skill → skill_embeddings（upsert，幂等）。"""
    skills = await asyncio.to_thread(_fetch_skill_rows)
    if not skills:
        return {"written": 0, "detail": "图谱无 Skill 节点"}

    names = [name for _, name in skills]
    vecs = await asyncio.to_thread(_embed_all, embedder, names)
    written = 0
    for (sid, name), vec in zip(skills, vecs):
        stmt = insert(SkillEmbedding).values(
            id=sid, embedding=vec, payload={"name": name}
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[SkillEmbedding.id],
            set_={"embedding": vec, "metadata": {"name": name}},
        )
        await db.execute(stmt)
        written += 1
    await db.commit()
    return {"written": written, "detail": "skill_embeddings 已回填"}


async def backfill_jd_embeddings(db: AsyncSession, embedder, limit: int | None = None) -> dict:
    """jd_raw → jd_embeddings（upsert 按 jd_id，幂等）。"""
    stmt = select(JDRaw).order_by(JDRaw.id.asc())
    rows = (await db.scalars(stmt)).all()
    if limit:
        rows = rows[:limit]

    records = []
    for r in rows:
        text = _jd_text(r.snapshot or {})
        if not text:
            continue
        records.append((str(r.id), text, dict(r.snapshot or {})))
    if not records:
        return {"written": 0, "detail": "jd_raw 无可用文本"}

    vecs = await asyncio.to_thread(_embed_all, embedder, [text for _, text, _ in records])
    written = 0
    for (jd_id, text, snap), vec in zip(records, vecs):
        meta = {
            "jd_id": jd_id,
            "title": snap.get("title", ""),
            "company": snap.get("company", ""),
            "city": snap.get("location", ""),
        }
        stmt = insert(JdEmbedding).values(
            jd_id=jd_id, embedding=vec, payload=meta,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[JdEmbedding.jd_id],
            set_={"embedding": vec, "metadata": meta},
        )
        await db.execute(stmt)
        written += 1
    await db.commit()
    return {"written": written, "detail": "jd_embeddings 已回填"}


async def backfill_project_embeddings(db: AsyncSession, embedder) -> dict:
    """resume_cache → project_embeddings（按 resume_id+project_index 幂等）。

    每份简历的项目重解析后会更新，先删后插保证旧项目向量不残留。
    """
    resumes = (await db.scalars(select(ResumeCache))).all()

    items: list[tuple[str, int, str, str, str]] = []
    for resume in resumes:
        projects = (resume.parsed_data or {}).get("projects") or []
        for idx, pr in enumerate(projects):
            if isinstance(pr, str):
                name, desc = pr, ""
            elif isinstance(pr, dict):
                name = pr.get("name", "")
                desc = pr.get("description", "")
            else:
                continue
            text = _project_text(name, desc)
            if not text:
                continue
            items.append((str(resume.id), idx, name, desc, text))
    if not items:
        return {"written": 0, "detail": "无简历项目数据"}

    vecs = await asyncio.to_thread(_embed_all, embedder, [text for *_, text in items])
    # 先删后插：简历项目重解析后集合变化（增删改），删除旧向量防残留
    resume_ids = {rid for rid, *_ in items}
    if resume_ids:
        await db.execute(
            ProjectEmbedding.__table__.delete().where(
                ProjectEmbedding.resume_id.in_(resume_ids)
            )
        )
    written = 0
    for (resume_id, idx, name, desc, text), vec in zip(items, vecs):
        meta = {
            "resume_id": resume_id,
            "project_index": idx,
            "project_name": name,
            "description": desc,
            "text": text,
        }
        db.add(ProjectEmbedding(
            resume_id=resume_id, project_index=idx, embedding=vec, payload=meta,
        ))
        written += 1
    await db.commit()
    return {"written": written, "detail": "project_embeddings 已回填"}


async def run_backfill(
    db: AsyncSession, embedder=None, *, skills: bool = True,
    jds: bool = True, projects: bool = True,
) -> dict:
    """一键回填三表（独立脚本 / ETL 阶段共用入口）。

    embedder 缺省用 SkillEmbedder 单例；模型不可用时抛 SemanticUnavailableError。
    """
    if embedder is None:
        embedder = SkillEmbedder.get()
    result: dict = {}
    if skills:
        result["skill_embeddings"] = await backfill_skill_embeddings(db, embedder)
    if jds:
        result["jd_embeddings"] = await backfill_jd_embeddings(db, embedder)
    if projects:
        result["project_embeddings"] = await backfill_project_embeddings(db, embedder)
    return result
