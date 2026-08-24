r"""每日图谱版本快照手动补跑入口（设计文档 §7.1 T+1 版本管理；08-23 P0-2）。

⚠️ 仅手动运维工具——快照发布已链入 run_etl_pipeline（backfill_embeddings
之后、evolved_from 之前，且受事实门禁保护）。禁止将本脚本装入
crontab / Windows 计划任务：独立触发会在主管线之外发布快照版本，
打乱「快照 → 演化推导 → 发现」顺序，且不经过事实门禁。

调用方式（手动补跑）：
    cd backend && uv run python scripts/cron/snapshot_daily.py

依赖：Redis 已启动、ARQ Worker 已运行（arq app.workers.settings.WorkerSettings）
"""

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 让脚本在无 site-packages editable install 时也能找到 app 模块
_BACKEND_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_BACKEND_DIR))

from app.core.logging import setup_logging

logger = setup_logging("cron.snapshot_daily")


async def enqueue_snapshot() -> None:
    """将图谱版本快照任务入队到 ARQ。"""
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
        job = await client.enqueue_job(
            "snapshot_graph",
            triggered_by="scheduled",
        )
        logger.info(f"[snapshot_daily] 已入队 snapshot_graph, job_id={job.job_id}")
    finally:
        await client.close()


def main() -> int:
    """脚本入口。

    返回 0 表示入队成功，非 0 表示失败（cron 可据此告警）。
    """
    cst = datetime.now(timezone(timedelta(hours=8)))
    logger.info("启动调度，CST=%s", cst.isoformat())
    try:
        asyncio.run(enqueue_snapshot())
        logger.info("调度完成")
        return 0
    except Exception:
        logger.exception("调度失败")
        return 1


if __name__ == "__main__":
    sys.exit(main())
