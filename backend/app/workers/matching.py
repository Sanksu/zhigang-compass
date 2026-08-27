"""Resume parsing and position matching ARQ tasks."""

import asyncio
import json
import logging
import uuid
from pathlib import Path

from sqlalchemy import select

from app.core import runtime_config
from app.models.business import MatchResultRecord, ResumeCache, TaskStatus

logger = logging.getLogger(__name__)

# Match result Redis snapshot TTL (kept aligned with app.api.v1.match).
_MATCH_RESULT_TTL = 24 * 60 * 60


async def resume_parse(
    ctx: dict,
    file_path: str,
    task_id: str | None = None,
) -> dict:
    """Parse a resume asynchronously and persist its cached profile."""
    from app.core.database import async_session_factory
    from app.services.resume.extractor import ResumeExtractor
    from app.services.resume.file_parser import extract_text
    from app.services.resume.pii_mask import mask_pii, restore_pii

    async with async_session_factory() as session:
        task = await session.get(TaskStatus, task_id) if task_id else None
        if task is None:
            # Support legacy jobs enqueued without task_id.
            task = await session.scalar(
                select(TaskStatus).where(
                    TaskStatus.result["file_path"].astext == str(file_path)
                )
            )
        if task is None:
            return {"status": "failed", "error": "TaskStatus 不存在"}

        result_info = task.result or {}
        task.status = "running"
        task.progress = 0.2
        await session.commit()

        try:
            # Extract and mask PII before sending any content to the LLM.
            text = await asyncio.to_thread(extract_text, file_path)
            masked, pii_mapping = await asyncio.to_thread(mask_pii, text)

            task.progress = 0.6
            await session.commit()
            result = await asyncio.to_thread(ResumeExtractor().extract, masked)

            parsed = await asyncio.to_thread(
                restore_pii,
                result.model_dump(),
                pii_mapping,
            )
            cache = await session.scalar(
                select(ResumeCache).where(
                    ResumeCache.file_hash == result_info["file_hash"]
                )
            )
            if cache is None:
                # Keep cache.id aligned with resume_files.resume_id for ownership checks.
                cache = ResumeCache(
                    id=task.id,
                    file_hash=result_info["file_hash"],
                    file_name=result_info.get("file_name") or Path(file_path).name,
                    parsed_data=parsed,
                )
                session.add(cache)
            else:
                cache.parsed_data = parsed
                cache.version += 1
            await session.flush()

            task.status = "success"
            task.progress = 1.0
            certs = [
                cert
                for cert in parsed.get("certifications", [])
                if cert.get("name")
            ]
            logger.info(
                "resume_parse 完成：resume_id=%s 技能=%d 证书=%d 证书明细=%s",
                str(cache.id),
                len(parsed.get("skills", [])),
                len(certs),
                [
                    {"name": cert.get("name"), "issuer": cert.get("issuer", "")}
                    for cert in certs[:10]
                ],
            )
            task.result = {
                "resume_id": str(cache.id),
                "skills": [
                    skill.get("name")
                    for skill in parsed.get("skills", [])
                    if skill.get("name")
                ],
            }
        except Exception as error:
            task.status = "failed"
            task.error = str(error)[:500]
        await session.commit()

        if task.status == "success":
            return {"status": "success", "resume_id": task.result["resume_id"]}
        return {"status": "failed", "error": task.error}


def _complete_recommend_result(
    previous: dict | None,
    match_id: str,
    top_n: int,
) -> dict:
    """Merge completion data without discarding enqueued ownership fields."""
    return {**(previous or {}), "match_id": match_id, "top_n": top_n}


async def _load_jd_evidence_rows(
    results: list,
) -> dict:
    """并发加载命中岗位族内 JD 行（stage B：JD 级证据）。

    results 去重 position_name 后并行查 jd_raw（limit 控制成本），失败单岗位
    降级为空（不影响匹配结果）。每岗位独立 session：SQLAlchemy AsyncSession
    禁止并发协程共用（第六轮审查 P1-1——此前 gather 共用同一 session，除
    首个外均抛并发错被吞，生产环境 jd_evidence 大面积静默为空）。
    """
    from app.core.database import async_session_factory
    from app.services.matching.jd_rerank import load_jd_rows_for_position

    names = list(
        dict.fromkeys(
            str(r.position_name) for r in results if r.position_name
        )
    )
    if not names:
        return {}

    async def _one(name: str) -> tuple[str, list]:
        try:
            async with async_session_factory() as s:
                rows = await load_jd_rows_for_position(s, name)
            return name, rows
        except Exception:
            logger.warning("[match/jd_evidence] 岗位 %s JD 加载失败，降级为空", name)
            return name, []

    gathered = await asyncio.gather(*(_one(n) for n in names))
    return dict(gathered)


async def _match_jd_candidates(
    session,
    candidate,
    project_vectors: dict,
    top_n: int,
) -> list[dict]:
    """阶段 C：原生 JD 候选匹配（全量 JD → 向量预筛 → 逐条评分 → 岗位级聚合）。

    候选源=jd_raw 抽取快照（非聚合岗位画像）：每条 JD 直接建成 PositionProfile
    评分（score_position 复用）。召回优先用「JD 技能池化向量」余弦 Top-K
    （Redis 缓存池化向量，矩阵点积毫秒级——修复全量评分 36s 性能墙：
    300 候选 × ~0.12s 评分 → 50 候选 ≈6s）；SBERT 不可用降级 rough_select
    技能命中粗选（旧行为）。结果按 snapshot.normalized_position 聚合回岗位级
    展示（Top-N 岗位 + 组内最佳 JD 证据），输出与聚合岗位模式同构。
    """
    from sqlalchemy import select

    from app.models.raw import JDRaw
    from app.services.matching.engine import RuleBasedMatcher
    from app.services.matching.jd_aggregate import aggregate_jd_scores
    from app.services.matching.jd_profiles import rough_select, rows_to_profiles
    from app.services.matching.jd_vector_recall import (
        candidate_vector,
        load_pool_vectors_cached,
        vector_recall,
    )
    from app.services.matching.schemas import MatchMode, MatchRequest
    from app.services.matching.semantic import SkillEmbedder

    rows = (await session.scalars(
        select(JDRaw)
        .where(JDRaw.snapshot["extraction"].astext.is_not(None))
        .order_by(JDRaw.id)  # 行序稳定：池化向量指纹顺序无关化的双保险
    )).all()
    jd_profiles, jd_position = rows_to_profiles(rows)
    if not jd_profiles:
        return []

    candidate_skills = [s.skill_name for s in candidate.skills if s.skill_name]
    rough_k = int(runtime_config.get("match_jd_rough_k", 50))

    # 向量预筛：池化向量加载（Redis 读写留主循环，CPU 段内部 to_thread）；
    # SBERT 不可用/缓存异常 → pool_vecs=None 降级 rough_select 技能命中粗选
    from app.core.database import redis_client

    embedder = SkillEmbedder.get()
    try:
        pool_vecs = await load_pool_vectors_cached(jd_profiles, embedder, redis_client)
    except Exception as e:
        logger.warning("[match/jd_mode] 池化向量加载失败（降级命中粗选）: %s", e)
        pool_vecs = None

    pool = None
    if pool_vecs:
        def _recall():
            cand_vec = candidate_vector(candidate_skills, embedder)
            if cand_vec is None:
                return None
            return vector_recall(jd_profiles, pool_vecs, cand_vec, k=rough_k)

        pool = await asyncio.to_thread(_recall)
    if pool is None:
        pool = rough_select(jd_profiles, candidate_skills, k=rough_k)
    if not pool:
        return []
    # 岗位多样性配额（第六轮审查算法口径 1）：同族 JD 池化向量高度相似可
    # 占满召回席位，聚合后岗位数远小于 top_n——按岗位名轮转配额后再评分
    from app.services.matching.jd_profiles import diversify_by_position

    pool = diversify_by_position(pool, jd_position, rough_k, top_n)

    def _score():
        matcher = RuleBasedMatcher(pool, semantic=SkillEmbedder.get())
        return matcher.match(
            MatchRequest(
                candidate=candidate,
                mode=MatchMode.AUTO,
                # MatchRequest.top_n 上限 100；召回内全量评分用上限值，
                # 聚合层再取真正的 top_n。
                top_n=min(len(pool), 100),
                project_vectors=project_vectors,
            )
        )

    scored = await asyncio.to_thread(_score)
    return aggregate_jd_scores(scored, jd_position, top_n=top_n)


async def match_recommend(
    ctx: dict,
    resume_id: str,
    top_n: int = 10,
    task_id: str | None = None,
    user_id: str = "",
) -> dict:
    """Recommend Top-N positions and persist the asynchronous task result."""
    from app.core.database import async_session_factory, redis_client
    from app.services.matching.engine import RuleBasedMatcher
    from app.services.matching.loaders import build_candidate
    from app.services.matching.schemas import MatchMode, MatchRequest
    from app.services.matching.semantic import SkillEmbedder
    from app.services.matching.shared_cache import load_positions_shared

    async with async_session_factory() as session:
        task = await session.get(TaskStatus, task_id) if task_id else None
        if task is None:
            return {"status": "failed", "error": "TaskStatus 不存在"}

        cache = await session.get(ResumeCache, resume_id)
        if cache is None:
            task.status = "failed"
            task.error = "简历不存在"
            await session.commit()
            return {"status": "failed", "error": "简历不存在"}

        task.status = "running"
        task.progress = 0.3
        await session.commit()

        try:
            candidate = build_candidate(cache.parsed_data)
            task.progress = 0.6
            await session.commit()

            from app.services.embeddings.vector_store import load_project_vectors

            project_vectors = await load_project_vectors(session, resume_id)
            # 阶段 C（JD 候选模式）：开关默认 False（灰度），开启后候选从
            # 「聚合岗位画像」切到「原生 JD 全量」（rough_select 预筛 → 逐条评分 →
            # 岗位级聚合），输出与聚合岗位模式同构（岗位 Top-N + jd_evidence）。
            jd_mode_enabled = (
                ctx.get("jd_candidates") is True
                or runtime_config.get("match_jd_candidates_enabled", False)
            )
            if jd_mode_enabled:
                data_items = await _match_jd_candidates(
                    session, candidate, project_vectors, top_n,
                )
            else:
                # 岗位画像走 Redis 版本化共享缓存（跨进程单飞；Redis 故障降级进程 TTL）
                positions = await load_positions_shared()

                def _match():
                    matcher = RuleBasedMatcher(
                        positions,
                        semantic=SkillEmbedder.get(),
                    )
                    return matcher.match(
                        MatchRequest(
                            candidate=candidate,
                            mode=MatchMode.AUTO,
                            top_n=top_n,
                            project_vectors=project_vectors,
                        )
                    )

                results = await asyncio.to_thread(_match)
                # 阶段 B：JD 级证据精排（每个命中岗位族内原生 JD 二次精排，附加
                # jd_evidence 到结果 dict，不动 MatchResult schema / 评分顺序）
                from app.services.matching.jd_rerank import (
                    enrich_with_jd_evidence,
                )

                jd_rows = await _load_jd_evidence_rows(results)
                candidate_skills = [
                    s.get("name")
                    for s in (cache.parsed_data or {}).get("skills", [])
                    if s.get("name")
                ]
                data_items = [result.model_dump() for result in results]
                enrich_with_jd_evidence(data_items, jd_rows, candidate_skills)

            match_id = str(uuid.uuid4())

            data = {
                "items": data_items,
                "match_id": match_id,
                "user_id": user_id,
            }
            await redis_client.set(
                f"match:result:{match_id}",
                json.dumps(data, ensure_ascii=False),
                ex=_MATCH_RESULT_TTL,
            )

            # PostgreSQL is a durable mirror; Redis remains the primary result store.
            try:
                row = await session.scalar(
                    select(MatchResultRecord).where(
                        MatchResultRecord.match_id == match_id
                    )
                )
                if row is None:
                    session.add(
                        MatchResultRecord(
                            match_id=match_id,
                            # JD 候选模式与聚合岗位模式统一从 data_items 取
                            # 首项岗位名（两模式输出同构 dict 列表）
                            position_name=(
                                data_items[0]["position_name"] if data_items else ""
                            ),
                            user_id=user_id,
                            result=data,
                        )
                    )
                else:
                    row.result = data
                await session.flush()
            except Exception:
                # 镜像落库失败仅降级（Redis 为主存），但必须 rollback——否则
                # session 残留 pending-rollback 态，末尾统一 commit 必抛
                # PendingRollbackError 逃逸、task.status 卡 running
                # （第六轮审查 P1-3）
                logger.exception("匹配结果落库失败，跳过（不影响任务成功）")
                await session.rollback()

            task.status = "success"
            task.progress = 1.0
            task.result = _complete_recommend_result(
                task.result,
                match_id,
                len(data_items),
            )
        except Exception as error:
            task.status = "failed"
            task.error = str(error)[:500]
        await session.commit()

        if task.status == "success":
            return {"status": "success", "match_id": task.result["match_id"]}
        return {"status": "failed", "error": task.error}
