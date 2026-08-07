"""每日新岗位发现 + 自动状态流转调度入口（设计文档 7.2.3 / 7.2.1）。

被系统 cron / Windows 计划任务调用，依次将发现与自动流转任务入队到 ARQ。

调用方式：
    # Linux cron（crontab -e，ETL/快照发布后运行）
    30 5 * * * cd /path/to/backend && uv run python scripts/cron/discovery_daily.py >> logs/discovery_$(date +\%Y\%m\%d).log 2>&1

    # Windows 计划任务（PowerShell，每日 05:30）
    cd backend; uv run python scripts/cron/discovery_daily.py

任务编排（入队顺序即 ARQ 消费顺序）：
    1. discovery_daily：聚合 jd_raw → 阶段一门控 → RAG 接地 → 候选池 upsert
    2. discovery_auto_transition：读 graph_versions 窗口序列 → emerging/stable/
       declining 自动流转判定 → Neo4j + 候选池幂等持久化

依赖：Redis 已启动、ARQ Worker 已运行（arq app.workers.tasks.WorkerSettings）、
ETL 主管线已完成（auto_transition 依赖当日快照参与窗口序列）。
"""

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 让脚本在无 site-packages editable install 时也能找到 app 模块
_BACKEND_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_BACKEND_DIR))


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
        print(f"[discovery_daily] 已入队 discovery_daily, job_id={job1.job_id}")
        job2 = await client.enqueue_job("discovery_auto_transition")
        print(f"[discovery_daily] 已入队 discovery_auto_transition, job_id={job2.job_id}")
    finally:
        await client.close()


def main() -> int:
    """脚本入口。

    返回 0 表示入队成功，非 0 表示失败（cron 可据此告警）。
    """
    cst = datetime.now(timezone(timedelta(hours=8)))
    print(f"[discovery_daily] 启动调度，CST={cst.isoformat()}")
    try:
        asyncio.run(enqueue_discovery())
        print("[discovery_daily] 调度完成")
        return 0
    except Exception as e:
        print(f"[discovery_daily] 调度失败: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
