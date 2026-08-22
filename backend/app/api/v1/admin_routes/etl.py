"""管理端 ETL 手动触发（快捷操作面板：数据清洗/聚合入图/完整管线）。

白名单 job 经统一包装 run_etl_job_manual 入队（TaskStatus 生命周期由
包装维护）；仿 crawl_trigger 模式——先建 TaskStatus(pending) 再投递 ARQ，
队列不可用即标记 failed。GET /etl/task/{task_id} 供前端轮询终态。
"""

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.common import iso, serialize_task
from app.core.arq_client import enqueue
from app.core.database import get_db
from app.core.errors import ERR_INTERNAL, ERR_NOT_FOUND, ERR_VALIDATION
from app.models.business import TaskStatus
from app.schemas.common import error, ok

logger = logging.getLogger(__name__)

router = APIRouter()

# 可触发任务白名单（label 供错误提示与前端展示）；
# 与 app.workers.etl._MANUAL_JOBS 同步维护（白名单双写点）
ETL_JOB_LABELS: dict[str, str] = {
    "dedup_simhash": "数据清洗（SimHash 近似去重）",
    "aggregate_positions": "聚合入图（岗位-技能图幂等覆盖写回 Neo4j）",
    "run_etl_pipeline": "完整 ETL 管线（采集→清洗→结构化→聚合入图→快照）",
}


class EtlTriggerRequest(BaseModel):
    """POST /admin/etl/trigger 请求体（契约先行）。"""

    job: str = Field(
        description="任务标识：dedup_simhash/aggregate_positions/run_etl_pipeline",
    )


@router.post("/etl/trigger", status_code=202)
async def etl_trigger(req: EtlTriggerRequest, db: AsyncSession = Depends(get_db)):
    """手动触发 ETL 任务（契约 /admin/etl/trigger，202 + task_id 轮询）。"""
    job = req.job.strip()
    if job not in ETL_JOB_LABELS:
        return error(
            ERR_VALIDATION,
            f"未知任务: {job}（可选: {', '.join(sorted(ETL_JOB_LABELS))}）",
        )

    task = TaskStatus(task_type="etl", status="pending", result={"job": job})
    db.add(task)
    await db.commit()
    await db.refresh(task)

    try:
        await enqueue("run_etl_job_manual", job_name=job, task_id=str(task.id))
    except Exception:
        task.status = "failed"
        task.error = "任务入队失败"  # 固定文案：详情仅入日志
        await db.commit()
        logger.exception("ETL 任务入队失败: task_id=%s job=%s", task.id, job)
        return error(ERR_INTERNAL, "任务入队失败，请稍后重试")

    logger.info("[etl/trigger] 任务已入队: task_id=%s job=%s", task.id, job)
    return ok(data={"task_id": task.id, "job": job, "status": "pending"})


@router.get("/etl/task/{task_id}")
async def etl_task_status(task_id: str, db: AsyncSession = Depends(get_db)):
    """ETL 任务状态查询（前端快捷操作按钮轮询终态）。"""
    task = (await db.scalars(
        select(TaskStatus).where(
            TaskStatus.id == task_id, TaskStatus.task_type == "etl"
        )
    )).first()
    if task is None:
        return error(ERR_NOT_FOUND, "任务不存在")
    return ok(data=serialize_task(task, extra={
        "created_at": iso(task.created_at),
        "updated_at": iso(task.updated_at),
    }))
