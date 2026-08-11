"""每日新岗位发现 + 自动状态流转调度入口（设计文档 7.2.3 / 7.2.1）。

日常调度：发现与自动流转已链入 run_etl_pipeline 阶段 15（快照发布之后），
保证 discovery_auto_transition 读到当日快照窗口序列；本脚本仅用于手动触发
或一次性补跑。

调用方式：
    # 手动触发
    cd backend && uv run python scripts/cron/discovery_daily.py

任务编排：
    1. discovery_daily：聚合 jd_raw → 阶段一门控 → RAG 接地 → 候选池 upsert
    2. discovery_auto_transition：读 graph_versions 窗口序列 → emerging/stable/
       declining 自动流转判定 → Neo4j + 候选池幂等持久化

依赖：Redis 已启动、ARQ Worker 已运行（arq app.workers.tasks.WorkerSettings）、
快照已发布（auto_transition 依赖快照参与窗口序列）。
"""

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 让脚本在无 site-packages editable install 时也能找到 app 模块
_BACKEND_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_BACKEND_DIR))

from app.core.logging import setup_logging

logger = setup_logging("cron.discovery_daily")


async def enqueue_discovery() -> None:
    """将新岗位发现与自动状态流转任务依次入队到 ARQ。"""
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
        job1 = await client.enqueue_job("discovery_daily")
        logger.info(f"[discovery_daily] 已入队 discovery_daily, job_id={job1.job_id}")
        # ARQ 多 worker 并发下 FIFO 不保证消费顺序，给 discovery_daily 5min
        # 领先时间再入队自动流转（避免 auto_transition 读到空候选池）
        job2 = await client.enqueue_job("discovery_auto_transition", _defer_by=300)
        logger.info(f"[discovery_daily] 已入队 discovery_auto_transition（延迟 5min）, job_id={job2.job_id}")
    finally:
        await client.close()


def main() -> int:
    """脚本入口。

    返回 0 表示入队成功，非 0 表示失败（cron 可据此告警）。
    """
    cst = datetime.now(timezone(timedelta(hours=8)))
    logger.info("启动调度，CST=%s", cst.isoformat())
    try:
        asyncio.run(enqueue_discovery())
        logger.info("调度完成")
        return 0
    except Exception:
        logger.exception("调度失败")
        return 1


if __name__ == "__main__":
    sys.exit(main())
