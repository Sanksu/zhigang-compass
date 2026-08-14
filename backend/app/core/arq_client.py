"""ARQ 队列客户端收敛（08-14 审查：admin/match/resume 三处池构建重复收敛至此）。

RedisSettings 从 settings.arq_redis_url 解析（日志不打印密码）；队列不可用抛
异常由调用方标记任务 failed，不静默吞错。

连接池为模块级懒建单例并跨请求复用：入队（低频）与 SSE 日志轮询（每 0.5s）
共用，避免反复 create_pool/close 的连接抖动。
"""

import asyncio
import logging
from urllib.parse import urlparse

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.core.config import settings

logger = logging.getLogger(__name__)

_pool: ArqRedis | None = None
_pool_lock = asyncio.Lock()


def _redis_settings() -> RedisSettings:
    parsed = urlparse(settings.arq_redis_url)
    return RedisSettings(
        host=parsed.hostname or "localhost",
        port=parsed.port or 6379,
        database=int(parsed.path.lstrip("/") or "1"),
        password=parsed.password,
    )


async def get_pool() -> ArqRedis:
    """模块级懒建 ARQ 连接池并复用（双检锁防并发重复建连）。"""
    global _pool
    if _pool is None:
        async with _pool_lock:
            if _pool is None:
                _pool = await create_pool(_redis_settings())
    return _pool


async def enqueue(job_name: str, **kwargs) -> None:
    """入队 ARQ 任务；队列不可用抛异常（由调用方标记 failed 并记录）。"""
    pool = await get_pool()
    try:
        await pool.enqueue_job(job_name, **kwargs)
    except Exception:
        logger.exception("[arq/enqueue] 入队失败: job=%s", job_name)
        raise
