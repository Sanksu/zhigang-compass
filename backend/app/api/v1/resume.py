"""简历路由：上传、解析任务、任务状态轮询、SSE 推送、编辑。

上传链路：文件 → SHA256 去重（命中 resume_cache 直接复用）→ 落盘 uploads/
→ 创建 TaskStatus → 入队 ARQ resume_parse 任务（PII 脱敏在任务内完成）。
"""

import asyncio
import hashlib
import json
import uuid
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_optional_user
from app.core.config import settings
from app.core.database import async_session_factory, get_db
from app.models.business import AuditLog, ResumeCache, TaskStatus
from app.schemas.common import ok, error

router = APIRouter()

# 上传目录（根 .gitignore 已忽略 uploads/，仅存运行时文件）
_UPLOAD_DIR = Path(__file__).resolve().parents[3] / "uploads"

# 上传边界：大小上限 10MB，类型白名单（防内存耗尽与任意文件写入）
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
ALLOWED_EXTENSIONS = {".pdf", ".doc", ".docx", ".txt"}


async def _enqueue_resume_parse(file_path: str, task_id: str) -> None:
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
        await pool.enqueue_job("resume_parse", file_path=file_path, task_id=task_id)
    finally:
        await pool.close()


@router.get("/list")
async def list_resumes(
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """已解析简历列表（最近 N 条，含候选人画像摘要）。

    供前端载入已有候选人发起匹配（recommend/compare 需要 resume_cache 记录）。
    """
    rows = await db.scalars(
        select(ResumeCache).order_by(ResumeCache.updated_at.desc()).limit(limit)
    )
    items = []
    for r in rows:
        parsed = r.parsed_data if isinstance(r.parsed_data, dict) else {}
        skills = parsed.get("skills", [])
        items.append({
            "id": r.id,
            "file_name": r.file_name,
            "skills": [s.get("name", s) if isinstance(s, dict) else s for s in skills],
            "total_years": parsed.get("total_years", 0),
            "education_level": parsed.get("education_level"),
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        })
    return ok(data={"items": items, "total": len(items)})


@router.post("/parse", status_code=202)
async def parse_resume(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """上传简历触发解析（异步任务，含 PII 脱敏预处理）。"""
    content = await file.read()
    if not content:
        return error(400, "文件为空")
    if len(content) > MAX_UPLOAD_BYTES:
        return error(413, f"文件超过 {MAX_UPLOAD_BYTES // (1024 * 1024)}MB 上限")

    suffix = Path(file.filename or "resume").suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        return error(415, "仅支持 pdf/doc/docx/txt 格式")

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
    file_path = _UPLOAD_DIR / f"{file_hash}{suffix}"
    file_path.write_bytes(content)

    task = TaskStatus(
        task_type="resume_parse",
        status="pending",
        result={
            "file_path": str(file_path),
            "file_hash": file_hash,
            "file_name": file.filename or "",
        },
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)

    try:
        await _enqueue_resume_parse(str(file_path), str(task.id))
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


def _parse_resume_id(resume_id: str) -> str | None:
    """校验并规范化简历 UUID，非法返回 None。"""
    try:
        return str(uuid.UUID(resume_id))
    except (ValueError, AttributeError, TypeError):
        return None


def _merge_fields(parsed: dict, fields: dict) -> dict:
    """顶层字段覆盖合并（编辑简历画像的字段更新语义）。"""
    merged = dict(parsed)
    merged.update(fields)
    return merged


def _sse_payload(task: TaskStatus) -> dict:
    """任务状态 → SSE data 载荷（TaskStatus ORM 对象不可直接 JSON 序列化）。"""
    return {
        "task_id": task.id,
        "task_type": task.task_type,
        "status": task.status,
        "progress": task.progress,
        "result": task.result,
        "error": task.error,
    }


async def _task_stream_events(
    task_uuid: str,
    get_task,
    *,
    poll_interval: float = 1.0,
    timeout: float = 300.0,
):
    """SSE 事件序列（可注入任务查询函数便于测试）。

    get_task: async callable(task_uuid) -> dict | None（已序列化载荷，见 _sse_payload）。
    事件流：progress 周期推送 → 终态 success/failed 推送 done/error 并结束；
    任务不存在 / 超时推送 error 后结束。
    """
    import time

    deadline = time.monotonic() + timeout
    while True:
        task = await get_task(task_uuid)
        if task is None:
            yield f"event: error\ndata: {json.dumps({'message': '任务不存在'}, ensure_ascii=False)}\n\n"
            return
        if task["status"] == "success":
            yield f"event: done\ndata: {json.dumps(task, ensure_ascii=False)}\n\n"
            return
        if task["status"] == "failed":
            yield f"event: error\ndata: {json.dumps(task, ensure_ascii=False)}\n\n"
            return
        yield f"event: progress\ndata: {json.dumps(task, ensure_ascii=False)}\n\n"
        if time.monotonic() >= deadline:
            yield f"event: error\ndata: {json.dumps({'message': '推送超时'}, ensure_ascii=False)}\n\n"
            return
        await asyncio.sleep(poll_interval)


@router.get("/{resume_id}")
async def get_resume(resume_id: str, db: AsyncSession = Depends(get_db)):
    """简历解析详情（FE-M4-04 个人中心"查看"：完整画像）。"""
    rid = _parse_resume_id(resume_id)
    if rid is None:
        return error(400, "resume_id 格式非法")
    resume = await db.get(ResumeCache, rid)
    if resume is None:
        return error(404, "简历不存在")
    return ok(data={
        "id": resume.id,
        "file_name": resume.file_name,
        "parsed_data": resume.parsed_data if isinstance(resume.parsed_data, dict) else {},
        "version": resume.version,
        "created_at": resume.created_at.isoformat() if resume.created_at else None,
        "updated_at": resume.updated_at.isoformat() if resume.updated_at else None,
    })


@router.put("/{resume_id}")
async def update_resume(
    resume_id: str,
    req: dict,
    db: AsyncSession = Depends(get_db),
    current_user: dict | None = Depends(get_optional_user),
):
    """编辑简历画像（BE-M4-01，契约 PUT /resume/{resume_id}）。

    LLM 抽取可能有误，允许用户手动修正。请求体 `{"fields": {...}}` 中的
    字段按顶层覆盖合并进 parsed_data（设计文档 §2.4.3），version 递增，
    写审计日志（认证用户存在时；匿名场景跳过，与现有 resume 端点无认证一致）。
    """
    rid = _parse_resume_id(resume_id)
    if rid is None:
        return error(400, "resume_id 格式非法")
    resume = await db.get(ResumeCache, rid)
    if resume is None:
        return error(404, "简历不存在")

    fields = req.get("fields")
    if not isinstance(fields, dict) or not fields:
        return error(400, "fields 必须为非空对象")

    resume.parsed_data = _merge_fields(resume.parsed_data or {}, fields)
    resume.version += 1

    if current_user:
        db.add(AuditLog(
            user_id=current_user.get("sub", ""),
            action="resume.update",
            resource="resume_cache",
            resource_id=rid,
            detail={"fields": list(fields.keys()), "version": resume.version},
        ))
    await db.commit()

    return ok(data={
        "id": resume.id,
        "file_name": resume.file_name,
        "parsed_data": resume.parsed_data,
        "version": resume.version,
        "updated_at": resume.updated_at.isoformat() if resume.updated_at else None,
    })


@router.get("/task/{task_id}/stream")
async def task_stream(task_id: str):
    """SSE 推送任务进度（BE-M4-01，契约 GET /resume/task/{task_id}/stream）。

    事件流：初始 status/progress → 周期推送 progress 事件 → 终态
    （success/failed）推送 done/error 事件并关闭。任务不存在推送 error 后关闭。
    轮询间隔 1s，上限 300s 兜底（防客户端挂起占用连接）。
    """
    try:
        task_uuid = str(uuid.UUID(task_id))
    except (ValueError, AttributeError):
        return error(400, "task_id 格式非法")

    async def _get_task(tid: str) -> dict | None:
        async with async_session_factory() as session:
            task = await session.get(TaskStatus, tid)
        return _sse_payload(task) if task is not None else None

    async def _event_gen():
        async for event in _task_stream_events(task_uuid, _get_task):
            yield event

    return StreamingResponse(_event_gen(), media_type="text/event-stream")


@router.delete("/{resume_id}", status_code=200)
async def delete_resume(resume_id: str, db: AsyncSession = Depends(get_db)):
    """删除简历记录及落盘文件（FE-M4-04 个人中心）。"""
    rid = _parse_resume_id(resume_id)
    if rid is None:
        return error(400, "resume_id 格式非法")
    resume = await db.get(ResumeCache, rid)
    if resume is None:
        return error(404, "简历不存在")

    # 删除落盘文件（uploads/{file_hash}{suffix}），文件缺失不阻塞删除
    for f in _UPLOAD_DIR.glob(f"{resume.file_hash}.*"):
        try:
            f.unlink()
        except OSError:
            pass

    await db.delete(resume)
    await db.commit()
    return ok(data={"deleted": True})
