"""ARQ worker shared utilities."""

from app.models.business import TaskStatus


_CRAWL_LOG_PREFIX = "crawl:log:"
_CRAWL_LOG_TTL_SECONDS = 3600


async def push_crawl_log(ctx: dict, task_id: str | None, line: str) -> None:
    """Append a crawler output line to the Redis log queue."""
    if not task_id or not line:
        return
    try:
        redis = ctx.get("redis")
        if redis is None:
            return
        key = _CRAWL_LOG_PREFIX + task_id
        await redis.pipeline().rpush(key, line).expire(key, _CRAWL_LOG_TTL_SECONDS).execute()
    except Exception:
        pass


async def update_crawl_task(task_id: str | None, **fields) -> None:
    """Update the optional TaskStatus record for a crawler task."""
    if not task_id:
        return
    from app.core.database import async_session_factory

    async with async_session_factory() as session:
        task = await session.get(TaskStatus, task_id)
        if task is None:
            return
        for field_name, value in fields.items():
            if field_name == "result" and isinstance(value, dict):
                task.result = {**(task.result or {}), **value}
            else:
                setattr(task, field_name, value)
        await session.commit()
