"""Asynchronous diagnosis report worker task."""

import asyncio
import json
import logging

from sqlalchemy import select

from app.models.business import (
    DiagnosisReportRecord,
    MatchResultRecord,
    TaskStatus,
)

logger = logging.getLogger(__name__)
_DIAGNOSIS_TTL = 24 * 60 * 60


async def generate_diagnosis(
    ctx: dict,
    match_id: str,
    task_id: str,
    user_id: str = "",
) -> dict:
    """Generate a diagnosis report asynchronously and persist its task state."""
    from app.core.database import async_session_factory, neo4j_driver, redis_client
    from app.services.diagnosis.generator import generate_diagnosis as generate_report
    from app.services.matching.semantic import SemanticUnavailableError, SkillEmbedder
    from app.services.rag.retrieval import retrieve_context

    async with async_session_factory() as session:
        task = await session.get(TaskStatus, task_id)
        if task is None:
            return {"status": "failed", "error": "TaskStatus 不存在"}
        task.status = "running"
        task.progress = 0.1
        await session.commit()
        try:
            cached = await redis_client.get(f"match:result:{match_id}")
            if cached is None:
                raise ValueError("匹配结果不存在或已过期")
            data = json.loads(cached)
            if data.get("user_id") != user_id:
                owner = await session.scalar(
                    select(MatchResultRecord.id).where(
                        MatchResultRecord.match_id == match_id,
                        MatchResultRecord.user_id == user_id,
                    )
                )
                if owner is None:
                    raise PermissionError("无权访问匹配结果")
            if not data.get("gaps"):
                raise ValueError("该匹配结果无差距数据")

            try:
                embedder = SkillEmbedder.get()
            except SemanticUnavailableError:
                embedder = None
            rag_chunks: list[dict] = []
            try:
                async with async_session_factory() as rag_session:
                    chunks = await retrieve_context(
                        data.get("position_name", ""),
                        rag_session,
                        neo4j=neo4j_driver,
                        embedder=embedder,
                    )
                rag_chunks = [chunk.__dict__ for chunk in chunks]
            except Exception:
                logger.exception("异步诊断 RAG 检索失败，继续无 RAG 生成")

            task.progress = 0.4
            await session.commit()
            report = await asyncio.to_thread(
                generate_report, data, rag_chunks=rag_chunks
            )
            payload = {"match_id": match_id, **report.model_dump()}
            await redis_client.set(
                f"match:diagnosis:{match_id}",
                json.dumps(payload, ensure_ascii=False),
                ex=_DIAGNOSIS_TTL,
            )

            report_row = await session.scalar(
                select(DiagnosisReportRecord).where(
                    DiagnosisReportRecord.match_id == match_id
                )
            )
            if report_row is None:
                session.add(
                    DiagnosisReportRecord(
                        match_id=match_id,
                        position_name=data.get("position_name", ""),
                        report=payload,
                    )
                )
            else:
                report_row.position_name = data.get("position_name", "")
                report_row.report = payload
            await session.flush()

            task.status = "success"
            task.progress = 1.0
            task.result = {
                "match_id": match_id,
                "report": payload,
                "user_id": user_id,
            }
        except Exception:
            logger.exception("诊断任务失败: task_id=%s match_id=%s", task_id, match_id)
            task.status = "failed"
            task.error = "诊断报告生成失败，请稍后重试"
        await session.commit()
        return {"status": task.status, "match_id": match_id, "error": task.error}
