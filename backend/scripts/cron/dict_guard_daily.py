"""dict-guard 每日字典守卫手动补跑入口。

日常调度：已链入 run_etl_pipeline 阶段 16（graph_health_check 之后），
继承主管线当日幂等锁；本脚本仅用于手动触发或一次性补跑（对齐
discovery_daily.py 模式）。

调用方式：
    cd backend && uv run python scripts/cron/dict_guard_daily.py

依赖：Redis 已启动、ARQ Worker 已运行（arq app.workers.settings.WorkerSettings）、
Neo4j/PostgreSQL 可达。
"""

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 让脚本在无 site-packages editable install 时也能找到 app 模块
_BACKEND_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_BACKEND_DIR))

from app.core.logging import setup_logging

logger = setup_logging("cron.dict_guard_daily")


async def enqueue_dict_guard() -> None:
    """将 dict-guard 每日评估任务入队到 ARQ。"""
    from arq import create_pool
    from arq.connections import RedisSettings

    import os
    from urllib.parse import urlparse

    redis_url = os.environ.get("ARQ_REDIS_URL", "redis://localhost:6379/1")
    parsed = urlparse(redis_url)
    redis_settings = RedisSettings(
        host=parsed.hostname or "localhost",
        port=parsed.port or 6379,
        database=int(parsed.path.lstrip("/") or "1"),
        password=parsed.password,
    )

    client = await create_pool(redis_settings)
    try:
        job = await client.enqueue_job("dict_guard_daily")
        logger.info(f"[dict_guard_daily] 已入队, job_id={job.job_id}")
    finally:
        await client.close()


def main() -> int:
    cst = datetime.now(timezone(timedelta(hours=8)))
    logger.info("启动 dict-guard 补跑，CST=%s", cst.isoformat())
    try:
        asyncio.run(enqueue_dict_guard())
        logger.info("入队完成")
        return 0
    except Exception:
        logger.exception("入队失败")
        return 1


if __name__ == "__main__":
    sys.exit(main())
