"""Resume parsing and position matching ARQ tasks."""

import asyncio
import json
import logging
import uuid
from pathlib import Path

from sqlalchemy import select

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
            match_id = str(uuid.uuid4())
            data = {
                "items": [result.model_dump() for result in results],
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
                            position_name=(
                                results[0].position_name if results else ""
                            ),
                            user_id=user_id,
                            result=data,
                        )
                    )
                else:
                    row.result = data
                await session.flush()
            except Exception:
                logger.exception("匹配结果落库失败，跳过（不影响任务成功）")

            task.status = "success"
            task.progress = 1.0
            task.result = _complete_recommend_result(
                task.result,
                match_id,
                len(results),
            )
        except Exception as error:
            task.status = "failed"
            task.error = str(error)[:500]
        await session.commit()

        if task.status == "success":
            return {"status": "success", "match_id": task.result["match_id"]}
        return {"status": "failed", "error": task.error}
