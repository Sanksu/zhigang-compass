"""每日 ETL 调度入口（设计文档 §4.4）。

被系统 cron / Windows 计划任务调用，将 ETL 任务入队到 ARQ。

调用方式：
    # Linux cron（crontab -e）
    0 2 * * * cd /path/to/backend && uv run python scripts/cron/etl_daily.py >> logs/etl_$(date +\%Y\%m\%d).log 2>&1

    # Windows 计划任务（PowerShell）
    cd backend; uv run python scripts/cron/etl_daily.py

任务分组（对齐设计文档 §4.4 数据更新频率）：
    02:00  国内 A/B 级招聘平台（boss/zhilian）
    04:00  国际 A/B 级招聘平台（indeed/glassdoor；monster 因 DataDome 防护不可绕过已停采）
    04:30  时效衰减重算（每日凌晨）
    05:00  ETL 主管线编排（含爬虫→清洗→去重→时滞→通胀→结构化→入图）

依赖：Redis 已启动、ARQ Worker 已运行（arq app.workers.tasks.WorkerSettings）
"""

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 让脚本在无 site-packages editable install 时也能找到 app 模块
_BACKEND_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_BACKEND_DIR))


async def enqueue_etl_pipeline() -> None:
    """将 ETL 主管线任务入队到 ARQ。

    通过 arq.Client.enqueue_job 触发 run_etl_pipeline，
    实际执行由 ARQ Worker 异步完成。
    """
    from arq import create_pool
    from arq.connections import RedisSettings

    # 从环境变量读取（与 app.core.config 一致）
    import os
    redis_url = os.environ.get("ARQ_REDIS_URL", "redis://localhost:6379/1")

    # 解析 redis://host:port/db 为 RedisSettings
    from urllib.parse import urlparse
    parsed = urlparse(redis_url)
    redis_settings = RedisSettings(
        host=parsed.hostname or "localhost",
        port=parsed.port or 6379,
        database=int(parsed.path.lstrip("/") or "1"),
        password=parsed.password,
    )

    client = await create_pool(redis_settings)
    try:
        run_date = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
        # 超时/重试由 WorkerSettings 中 func(run_etl_pipeline, timeout=10800,
        # max_tries=1) 的 per-function 配置负责（arq enqueue 不接收 _timeout/
        # _max_tries，传了会被当作任务参数导致 TypeError）。单源失败已在阶段内
        # 捕获，整管线重跑会重复爬取/重复抽取（入图幂等但网络与算力浪费）。
        job = await client.enqueue_job(
            "run_etl_pipeline",
            run_date=run_date,
        )
        print(f"[etl_daily] 已入队 run_etl_pipeline，run_date={run_date}, job_id={job.job_id}")
    finally:
        await client.close()


def main() -> int:
    """脚本入口。

    返回 0 表示入队成功，非 0 表示失败（cron 可据此告警）。
    """
    print(f"[etl_daily] 启动调度，CST={datetime.now(timezone(timedelta(hours=8))).isoformat()}")
    try:
        asyncio.run(enqueue_etl_pipeline())
        print("[etl_daily] 调度完成")
        return 0
    except Exception as e:
        print(f"[etl_daily] 调度失败: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
