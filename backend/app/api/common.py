"""API 层公共样板：分页执行/响应、任务序列化、简历归属校验、UUID 规范化、SSE 事件骨架。

各 router 曾各自实现的分页、TaskStatus 序列化、简历归属校验、SSE 轮询在此收敛，
字段与响应结构逐字节保持既有契约（openapi.yaml 不动）。
"""

import asyncio
import json
import time
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business import ResumeFile, TaskStatus
from app.schemas.common import ok


def iso(dt) -> str | None:
    """datetime → ISO 字符串（None 保持 None，API 响应字段统一序列化口径）。"""
    return dt.isoformat() if dt else None


def parse_uuid(raw: str) -> str | None:
    """校验并规范化 UUID 输入（非法返回 None）。"""
    try:
        return str(uuid.UUID(raw))
    except (ValueError, AttributeError, TypeError):
        return None


async def owns_resume(db: AsyncSession, resume_id: str, user_id: str) -> bool:
    """校验当前用户是否拥有该简历（resume_cache 无 user_id，归属记录在 resume_files）。"""
    row = await db.scalar(
        select(ResumeFile.id).where(
            ResumeFile.resume_id == resume_id, ResumeFile.user_id == user_id
        )
    )
    return row is not None


async def paginate(
    db: AsyncSession,
    stmt,
    page: int,
    size: int,
    *,
    count_stmt=None,
) -> tuple[list, int]:
    """分页执行 select：返回 (rows, total)。

    stmt 须已含 order_by（不含 offset/limit，本函数统一追加）；
    count_stmt 缺省时按 stmt 子查询计数（带 distinct/join 的语句也正确）。
    """
    if count_stmt is None:
        # 注意：不能用 `count_stmt or ...`——SQLAlchemy Select 的 __bool__ 会抛 TypeError
        count_stmt = select(func.count()).select_from(stmt.subquery())
    total = await db.scalar(count_stmt)
    result = await db.scalars(stmt.offset((page - 1) * size).limit(size))
    # AsyncScalarResult.all() 取行；测试注入的 list mock 无 .all()，直接使用
    rows = result.all() if hasattr(result, "all") else result
    return rows, total or 0


def paged_ok(items, total: int, page: int, size: int):
    """分页统一响应（契约字段：items/total/page/size）。"""
    return ok(data={"items": items, "total": total, "page": page, "size": size})


def serialize_task(
    task: TaskStatus,
    *,
    exclude: tuple[str, ...] = (),
    strip_fields: tuple[str, ...] = (),
    extra: dict | None = None,
) -> dict:
    """TaskStatus → API/SSE 载荷（ORM 对象不可直接 JSON 序列化）。

    strip_fields：从 result 快照中剔除的键（如服务端绝对路径）；
    exclude：从响应体剔除的顶层字段（如 match 轮询不需要 task_type/result）；
    extra：追加字段（如 success 时的 match_id、created_at/updated_at）。
    """
    result = dict(task.result or {})
    for f in strip_fields:
        result.pop(f, None)
    data = {
        "task_id": task.id,
        "task_type": task.task_type,
        "status": task.status,
        "progress": task.progress,
        "result": result,
        "error": task.error,
    }
    for f in exclude:
        data.pop(f, None)
    if extra:
        data.update(extra)
    return data


async def sse_task_events(
    task_uuid: str,
    get_task,
    *,
    before_poll=None,
    progress_payload=None,
    poll_interval: float = 1.0,
    timeout: float = 300.0,
):
    """SSE 任务状态事件序列公共骨架（resume 进度 / admin 爬虫日志共用）。

    get_task: async callable(task_uuid) -> dict | None（已序列化载荷）。
    before_poll: async callable() -> list[str]，每次轮询前执行并原样 yield
        （如爬虫日志增量拉取的事件帧）；缺省跳过。
    progress_payload: callable(task) -> dict，progress 事件载荷；缺省推送完整 task。
    事件流：progress 周期推送 → 终态 success/failed 推送 done/error 并结束；
    任务不存在 / 超时推送 error 后结束。
    """
    deadline = time.monotonic() + timeout
    while True:
        if before_poll is not None:
            for event in await before_poll():
                yield event
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
        payload = progress_payload(task) if progress_payload else task
        yield f"event: progress\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
        if time.monotonic() >= deadline:
            yield f"event: error\ndata: {json.dumps({'message': '推送超时'}, ensure_ascii=False)}\n\n"
            return
        await asyncio.sleep(poll_interval)
