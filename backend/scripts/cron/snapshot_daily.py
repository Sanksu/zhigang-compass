"""每日图谱版本快照调度入口（设计文档 §7.1 T+1 版本管理）。

被系统 cron / Windows 计划任务调用，将快照任务入队到 ARQ。

调用方式：
    # Linux cron（crontab -e，05:00 前发布当日版本）
    0 5 * * * cd /path/to/backend && uv run python scripts/cron/snapshot_daily.py >> logs/snapshot_$(date +\%Y\%m\%d).log 2>&1

    # Windows 计划任务（PowerShell，每日 05:00）
    cd backend; uv run python scripts/cron/snapshot_daily.py

依赖：Redis 已启动、ARQ Worker 已运行（arq app.workers.tasks.WorkerSettings）
"""

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 让脚本在无 site-packages editable install 时也能找到 app 模块
_BACKEND_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_BACKEND_DIR))


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
        print(f"[snapshot_daily] 已入队 snapshot_graph, job_id={job.job_id}")
    finally:
        await client.close()


def main() -> int:
    """脚本入口。

    返回 0 表示入队成功，非 0 表示失败（cron 可据此告警）。
    """
    cst = datetime.now(timezone(timedelta(hours=8)))
    print(f"[snapshot_daily] 启动调度，CST={cst.isoformat()}")
    try:
        asyncio.run(enqueue_snapshot())
        print("[snapshot_daily] 调度完成")
        return 0
    except Exception as e:
        print(f"[snapshot_daily] 调度失败: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
