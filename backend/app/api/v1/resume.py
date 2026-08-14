"""简历路由：上传、解析任务、任务状态轮询、SSE 推送、编辑。

上传链路：文件 → SHA256 去重（命中 resume_cache 直接复用）→ 落盘 uploads/
→ 原始文件字节落库 resume_files（仅上传者本人可下载）→ 创建 TaskStatus
→ 入队 ARQ resume_parse 任务（PII 脱敏在任务内完成）。
"""

import asyncio
import hashlib
import json
import logging
import uuid
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import String, cast, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_role
from app.core.arq_client import enqueue
from app.core.database import async_session_factory, get_db
from app.models.business import AuditLog, ResumeCache, ResumeFile, TaskStatus
from app.schemas.common import ok, error
from app.services.resume.file_parser import SUPPORTED_EXTENSIONS

router = APIRouter()

logger = logging.getLogger(__name__)

# 上传目录（根 .gitignore 已忽略 uploads/，仅存运行时文件）
_UPLOAD_DIR = Path(__file__).resolve().parents[3] / "uploads"

# 上传边界：大小上限 10MB，类型白名单（防内存耗尽与任意文件写入）。
# 白名单以解析器支持能力为单一事实源，避免上传通过后解析却失败的漂移（T-03）
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
ALLOWED_EXTENSIONS = SUPPORTED_EXTENSIONS


async def _enqueue_resume_parse(file_path: str, task_id: str) -> None:
    """入队 ARQ resume_parse 任务。

    队列不可用时抛出异常由调用方处理（标记任务 failed），不静默吞错。
    """
    await enqueue("resume_parse", file_path=file_path, task_id=task_id)


async def _persist_resume_file(
    db: AsyncSession,
    *,
    resume_id: str,
    user_id: str,
    file_hash: str,
    file_name: str,
    content_type: str,
    content: bytes,
) -> None:
    """简历原始文件落库（设计文档 §8.1：resume_files 留存，仅上传者本人可下载）。

    上传时同步写行（命中解析缓存的分支不写），删除简历时联动删除。
    """
    db.add(
        ResumeFile(
            resume_id=resume_id,
            user_id=user_id,
            file_hash=file_hash,
            file_name=file_name,
            content_type=content_type,
            content=content,
            file_size=len(content),
        )
    )


async def _owns_resume(db: AsyncSession, resume_id: str, user_id: str) -> bool:
    """校验当前用户是否拥有该简历（resume_cache 无 user_id，归属记录在 resume_files）。"""
    row = await db.scalar(
        select(ResumeFile.id).where(
            ResumeFile.resume_id == resume_id, ResumeFile.user_id == user_id
        )
    )
    return row is not None


async def _user_owns_task(db: AsyncSession, task: TaskStatus, user_id: str) -> bool:
    """任务归属校验：resume_parse 任务的 task_id 即 resume_id，按简历归属判定；
    其余任务类型（批量采集等系统任务）不向普通用户暴露。"""
    if task.task_type != "resume_parse":
        return False
    return await _owns_resume(db, task.id, user_id)


@router.get("/list")
async def list_resumes(
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("user")),
):
    """已解析简历列表（最近 N 条，含候选人画像摘要）。

    供前端载入已有候选人发起匹配（recommend/compare 需要 resume_cache 记录）。
    归属过滤：resume_cache 无 user_id，仅返回当前用户有 ResumeFile 归属的简历。
    """
    rows = await db.scalars(
        select(ResumeCache)
        .join(ResumeFile, ResumeFile.resume_id == cast(ResumeCache.id, String))
        .where(ResumeFile.user_id == user.get("sub", ""))
        .order_by(ResumeCache.updated_at.desc())
        .limit(limit)
        .distinct()
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
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("user")),
):
    """上传简历触发解析（异步任务，含 PII 脱敏预处理）。"""
    # 上传 DoS 防护（08-14）：读流前按 Content-Length 预检，超大文件直接拒绝，
    # 避免恶意超大流全量读入内存耗尽服务
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > MAX_UPLOAD_BYTES:
        return error(4000, f"文件超过 {MAX_UPLOAD_BYTES // (1024 * 1024)}MB 上限", http_status=413)
    content = await file.read()
    if not content:
        return error(4000, "文件为空")
    if len(content) > MAX_UPLOAD_BYTES:
        return error(4000, f"文件超过 {MAX_UPLOAD_BYTES // (1024 * 1024)}MB 上限")

    suffix = Path(file.filename or "resume").suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        supported = "/".join(sorted(ext.lstrip(".") for ext in ALLOWED_EXTENSIONS))
        hint = "，.doc 请转存为 .docx" if suffix == ".doc" else ""
        return error(4000, f"仅支持 {supported} 格式{hint}")

    file_hash = hashlib.sha256(content).hexdigest()

    # 命中缓存直接复用，不重复解析
    cached = await db.scalar(
        select(ResumeCache).where(ResumeCache.file_hash == file_hash)
    )
    if cached is not None:
        # 缓存按内容哈希全局唯一；命中时补建当前用户归属记录，
        # 否则他人上传的文件复用后当前用户将无 ResumeFile 关联、无法访问
        if not await _owns_resume(db, cached.id, user.get("sub", "")):
            await _persist_resume_file(
                db,
                resume_id=cached.id,
                user_id=user.get("sub", ""),
                file_hash=file_hash,
                file_name=file.filename or "",
                content_type=file.content_type or "",
                content=content,
            )
            await db.commit()
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
    await db.flush()  # 先落 task 拿 id，作为 resume_files.resume_id（前端 resume_id 即任务 id）

    # 原始文件字节落库（§8.1：仅上传者本人可下载），与任务同事务提交保证原子性
    await _persist_resume_file(
        db,
        resume_id=str(task.id),
        user_id=user.get("sub", ""),
        file_hash=file_hash,
        file_name=file.filename or "",
        content_type=file.content_type or "",
        content=content,
    )
    await db.commit()
    await db.refresh(task)

    try:
        await _enqueue_resume_parse(str(file_path), str(task.id))
    except Exception as e:
        task.status = "failed"
        task.error = "任务入队失败"  # 固定文案：详情仅入日志，防经 /resume/task 透传内部信息
        await db.commit()
        logger.error(f"[resume/parse] 任务入队失败: task_id={task.id} err={e}")

    return ok(data={"task_id": task.id, "resume_id": task.id, "cached": False})


@router.get("/task/{task_id}")
async def task_status(task_id: str, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("user"))):
    """轮询异步任务状态。"""
    try:
        task_uuid = str(uuid.UUID(task_id))
    except (ValueError, AttributeError):
        return error(4000, "task_id 格式非法")
    task = await db.get(TaskStatus, task_uuid)
    if task is None:
        return error(4040, "任务不存在", http_status=404)
    if not await _user_owns_task(db, task, user.get("sub", "")):
        return error(4030, "无权查看该任务", http_status=403)
    result = dict(task.result or {})
    result.pop("file_path", None)  # 不向客户端暴露服务端绝对路径
    return ok(data={
        "task_id": task.id,
        "task_type": task.task_type,
        "status": task.status,
        "progress": task.progress,
        "result": result,
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
    result = dict(task.result or {})
    result.pop("file_path", None)  # 不向客户端暴露服务端绝对路径
    return {
        "task_id": task.id,
        "task_type": task.task_type,
        "status": task.status,
        "progress": task.progress,
        "result": result,
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
async def get_resume(resume_id: str, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("user"))):
    """简历解析详情（FE-M4-04 个人中心"查看"：完整画像）。"""
    rid = _parse_resume_id(resume_id)
    if rid is None:
        return error(4000, "resume_id 格式非法")
    resume = await db.get(ResumeCache, rid)
    if resume is None:
        return error(4040, "简历不存在", http_status=404)
    if not await _owns_resume(db, rid, user.get("sub", "")):
        return error(4030, "无权访问该简历", http_status=403)
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
    user: dict = Depends(require_role("user")),
):
    """编辑简历画像（BE-M4-01，契约 PUT /resume/{resume_id}）。

    LLM 抽取可能有误，允许用户手动修正。请求体 `{"fields": {...}}` 中的
    字段按顶层覆盖合并进 parsed_data（设计文档 §2.4.3），version 递增，
    写审计日志（登录用户）。端点要求 user+ 角色（设计文档 §2.4.3）。
    """
    rid = _parse_resume_id(resume_id)
    if rid is None:
        return error(4000, "resume_id 格式非法")
    resume = await db.get(ResumeCache, rid)
    if resume is None:
        return error(4040, "简历不存在", http_status=404)
    if not await _owns_resume(db, rid, user.get("sub", "")):
        return error(4030, "无权修改该简历", http_status=403)

    fields = req.get("fields")
    if not isinstance(fields, dict) or not fields:
        return error(4000, "fields 必须为非空对象")

    resume.parsed_data = _merge_fields(resume.parsed_data or {}, fields)
    resume.version += 1

    db.add(AuditLog(
        user_id=user.get("sub", ""),
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
async def task_stream(task_id: str, user: dict = Depends(require_role("user"))):
    """SSE 推送任务进度（BE-M4-01，契约 GET /resume/task/{task_id}/stream）。

    事件流：初始 status/progress → 周期推送 progress 事件 → 终态
    （success/failed）推送 done/error 事件并关闭。任务不存在推送 error 后关闭。
    轮询间隔 1s，上限 300s 兜底（防客户端挂起占用连接）。
    """
    try:
        task_uuid = str(uuid.UUID(task_id))
    except (ValueError, AttributeError):
        return error(4000, "task_id 格式非法")

    async def _get_task(tid: str) -> dict | None:
        async with async_session_factory() as session:
            task = await session.get(TaskStatus, tid)
            if task is None:
                return None
            # SSE 与轮询端点同权：仅当前用户拥有的 resume_parse 任务可订阅
            if not await _user_owns_task(session, task, user.get("sub", "")):
                return None
            payload = _sse_payload(task)
        return payload

    async def _event_gen():
        async for event in _task_stream_events(task_uuid, _get_task):
            yield event

    return StreamingResponse(_event_gen(), media_type="text/event-stream")


@router.delete("/{resume_id}", status_code=204)
async def delete_resume(resume_id: str, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("user"))):
    """删除简历记录及落盘文件（FE-M4-04 个人中心）。"""
    rid = _parse_resume_id(resume_id)
    if rid is None:
        return error(4000, "resume_id 格式非法")
    resume = await db.get(ResumeCache, rid)
    if resume is None:
        return error(4040, "简历不存在", http_status=404)
    if not await _owns_resume(db, rid, user.get("sub", "")):
        return error(4030, "无权删除该简历", http_status=403)

    # 仅删除当前用户的归属记录；resume_cache 按内容哈希全局唯一，
    # 其他用户命中同一缓存时仍有归属，不得连坐删除
    await db.execute(
        delete(ResumeFile).where(
            ResumeFile.resume_id == rid, ResumeFile.user_id == user.get("sub", "")
        )
    )

    other_owner = await db.scalar(
        select(ResumeFile.id).where(ResumeFile.resume_id == rid)
    )
    if other_owner is None:
        # 无其他用户引用时删除落盘文件与缓存记录（文件缺失不阻塞删除）
        for f in _UPLOAD_DIR.glob(f"{resume.file_hash}.*"):
            try:
                f.unlink()
            except OSError:
                pass
        await db.delete(resume)
    await db.commit()
    # 契约 DELETE /resume/{id} 为 204：无响应体，前端仅据状态码判断成功
    return Response(status_code=204)


async def _fetch_resume_file(db: AsyncSession, resume_id: str) -> ResumeFile | None:
    """按 resume_id 查询原始文件行（resume_id 已校验为 UUID）。"""
    return await db.scalar(select(ResumeFile).where(ResumeFile.resume_id == resume_id))


def _download_disposition(filename: str) -> str:
    """下载响应 Content-Disposition（与 starlette FileResponse 同格式）。

    非 ASCII 文件名走 RFC 5987 filename*（引号内无法安全放中文/空格）。
    """
    quoted = quote(filename)
    if quoted == filename:
        return f'attachment; filename="{filename}"'
    return f"attachment; filename*=utf-8''{quoted}"


@router.get("/files/{resume_id}/download")
async def download_resume_file(
    resume_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("user")),
):
    """下载简历原始文件（设计文档 §8.1：仅上传者本人可下载，管理员无权访问原文）。

    文件字节从 resume_files 表读取（DB 留存），不依赖 uploads/ 落盘文件。
    注：starlette 1.3.1 的 FileResponse 仅支持真实文件路径，DB 字节下载用
    Response + 同格式 Content-Disposition 实现同等语义。
    """
    rid = _parse_resume_id(resume_id)
    if rid is None:
        return error(4000, "resume_id 格式非法")
    row = await _fetch_resume_file(db, rid)
    if row is None:
        return error(4040, "简历文件不存在", http_status=404)
    if row.user_id != user.get("sub", ""):
        return error(4030, "无权下载该简历文件", http_status=403)
    return Response(
        content=row.content,
        media_type=row.content_type or "application/octet-stream",
        headers={"Content-Disposition": _download_disposition(row.file_name)},
    )
