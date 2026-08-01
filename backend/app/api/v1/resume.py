"""简历路由：上传、解析任务、任务状态轮询。

上传链路：文件 → SHA256 去重（命中 resume_cache 直接复用）→ 落盘 uploads/
→ 创建 TaskStatus → 入队 ARQ resume_parse 任务（PII 脱敏在任务内完成）。
"""

import hashlib
import uuid
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.business import ResumeCache, TaskStatus
from app.schemas.common import ok, error

router = APIRouter()

# 上传目录（根 .gitignore 已忽略 uploads/，仅存运行时文件）
_UPLOAD_DIR = Path(__file__).resolve().parents[3] / "uploads"


async def _enqueue_resume_parse(file_path: str) -> None:
    """入队 ARQ resume_parse 任务。

    队列不可用时抛出异常由调用方处理（标记任务 failed），不静默吞错。
    """
    from arq import create_pool
    from arq.connections import RedisSettings

    parsed = urlparse(settings.arq_redis_url)
    redis_settings = RedisSettings(
        host=parsed.hostname or "localhost",
        port=parsed.port or 6379,
        database=int(parsed.path.lstrip("/") or "1"),
        password=parsed.password,
    )
    pool = await create_pool(redis_settings)
    try:
        await pool.enqueue_job("resume_parse", file_path=file_path)
    finally:
        await pool.close()


@router.post("/parse", status_code=202)
async def parse_resume(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """上传简历触发解析（异步任务，含 PII 脱敏预处理）。"""
    content = await file.read()
    if not content:
        return error(400, "文件为空")

    file_hash = hashlib.sha256(content).hexdigest()

    # 命中缓存直接复用，不重复解析
    cached = await db.scalar(
        select(ResumeCache).where(ResumeCache.file_hash == file_hash)
    )
    if cached is not None:
        return ok(data={
            "task_id": cached.id,
            "resume_id": cached.id,
            "cached": True,
        })

    _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    suffix = Path(file.filename or "resume").suffix
    file_path = _UPLOAD_DIR / f"{file_hash}{suffix}"
    file_path.write_bytes(content)

    task = TaskStatus(
        task_type="resume_parse",
        status="pending",
        result={"file_path": str(file_path), "file_hash": file_hash},
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)

    try:
        await _enqueue_resume_parse(str(file_path))
    except Exception as e:
        task.status = "failed"
        task.error = f"任务入队失败: {e}"
        await db.commit()

    return ok(data={"task_id": task.id, "resume_id": task.id, "cached": False})


@router.get("/task/{task_id}")
async def task_status(task_id: str, db: AsyncSession = Depends(get_db)):
    """轮询异步任务状态。"""
    try:
        task_uuid = str(uuid.UUID(task_id))
    except (ValueError, AttributeError):
        return error(400, "task_id 格式非法")
    task = await db.get(TaskStatus, task_uuid)
    if task is None:
        return error(404, "任务不存在")
    return ok(data={
        "task_id": task.id,
        "task_type": task.task_type,
        "status": task.status,
        "progress": task.progress,
        "result": task.result,
        "error": task.error,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
    })
