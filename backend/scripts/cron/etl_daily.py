r"""每日 ETL 手动补跑入口（设计文档 §4.4；08-23 闭环收敛 P0-2）。

⚠️ 仅手动运维工具——生产调度唯一事实源是 WorkerSettings 容器内 ARQ cron
（runtime_config.etl_run_hour/minute 控制，worker 重启后生效）。
禁止再将本脚本装入 crontab / Windows 计划任务：外部入队绕过
run_etl_pipeline_scheduled 包装（语义分叉），--force 可覆盖当日幂等锁
造成双跑；快照/演化顺序也会被独立触发打乱。

调用方式（手动补跑）：
    cd backend && uv run python scripts/cron/etl_daily.py            # 常规（幂等）
    cd backend && uv run python scripts/cron/etl_daily.py --force    # 失败重跑（覆盖当日锁）

任务分组（对齐设计文档 §4.4 数据更新频率）：
    02:00  国内 A/B 级招聘平台（boss/zhilian）
    04:00  国际 A/B 级招聘平台（indeed/glassdoor；monster 因 DataDome 防护不可绕过已停采）
    04:30  时效衰减重算（每日凌晨）
    05:00  ETL 主管线编排（含爬虫→清洗→去重→时滞→通胀→结构化→入图）

幂等：同 run_date 仅允许一次入队（Redis SET NX 锁 arq:etl:run:{date}，24h TTL），
重复触发直接跳过；失败重跑加 --force 覆盖。

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

logger = setup_logging("cron.etl_daily")


async def enqueue_etl_pipeline(force: bool = False) -> None:
    """将 ETL 主管线任务入队到 ARQ。

    通过 arq.Client.enqueue_job 触发 run_etl_pipeline，
    实际执行由 ARQ Worker 异步完成。

    force=True（--force）：跳过同 run_date 幂等锁，强制重新入队
    （失败重跑场景；正常调度不传，避免同日双实例并发）。
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
        # 同 run_date 幂等锁（P0-2）：05:00 计划任务入队后，若 05:00 的任务仍
        # 在队列/执行中，手动或重复触发会再入队一个 run_etl_pipeline，阶段 1
        # 双爬虫实例并发打同一源站（08-13 实测 zhilian 双实例）。SET NX 原子
        # 占位，24h TTL 覆盖当日窗口；任务失败重试需显式 --force 释放后重入。
        lock_key = f"arq:etl:run:{run_date}"
        if not force:
            # redis-py set 参数为 ex=（秒），非 expire=（08-14 修复：05:00 计划任务
            # 会因 TypeError 崩溃，幂等锁形同虚设）
            acquired = await client.set(lock_key, "1", nx=True, ex=60 * 60 * 24)
            if not acquired:
                logger.warning("当日 ETL 已在队列/执行中（%s），跳过重复触发（如需强制重跑请用 --force）", run_date)
                return
        # 超时/重试由 WorkerSettings 中 func(run_etl_pipeline, timeout=10800,
        # max_tries=1) 的 per-function 配置负责（arq enqueue 不接收 _timeout/
        # _max_tries，传了会被当作任务参数导致 TypeError）。单源失败已在阶段内
        # 捕获，整管线重跑会重复爬取/重复抽取（入图幂等但网络与算力浪费）。
        job = await client.enqueue_job(
            "run_etl_pipeline",
            run_date=run_date,
        )
        logger.info("已入队 run_etl_pipeline，run_date=%s, job_id=%s", run_date, job.job_id)
    finally:
        await client.close()


def main() -> int:
    """脚本入口。

    返回 0 表示入队成功（含幂等锁命中跳过），非 0 表示失败（cron 可据此告警）。
    """
    import argparse

    parser = argparse.ArgumentParser(description="每日 ETL 入队（同 run_date 幂等）")
    parser.add_argument("--force", action="store_true", help="跳过幂等锁强制重新入队（失败重跑场景）")
    args = parser.parse_args()

    logger.info("启动调度，CST=%s", datetime.now(timezone(timedelta(hours=8))).isoformat())
    try:
        asyncio.run(enqueue_etl_pipeline(force=args.force))
        logger.info("调度完成")
        return 0
    except Exception:
        logger.exception("调度失败")
        return 1


if __name__ == "__main__":
    sys.exit(main())
