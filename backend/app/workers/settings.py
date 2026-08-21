"""ARQ worker registration and lifecycle settings."""

import asyncio
import logging

from arq.connections import RedisSettings
from arq.cron import cron
from arq.worker import func

from app.core import runtime_config
from app.core.config import settings
from app.workers.tasks import (
    aggregate_positions,
    backfill_embeddings,
    batch_extract,
    check_data_freshness,
    check_llm_providers_health,
    crawl_platform,
    crawl_scheduler,
    cross_validate_jds,
    dedup_simhash,
    detect_inflation,
    dict_guard_daily,
    discovery_auto_transition,
    discovery_daily,
    diversity_report,
    enrich_course_skills,
    evaluate_courses,
    generate_diagnosis,
    graph_health_check,
    load_courses,
    match_recommend,
    resume_parse,
    run_etl_pipeline,
    run_etl_pipeline_scheduled,
    snapshot_graph,
    sync_skill_normalization,
    validate_temporal,
    watch_signal_daily,
)


logger = logging.getLogger(__name__)


async def on_startup(ctx: dict) -> None:
    """Preload the OCR engine without blocking worker startup."""
    logger.info("ARQ worker 启动，PID=%s", ctx.get("worker_pid"))

    async def warm_ocr() -> None:
        try:
            from app.services.resume import file_parser

            file_parser._ocr_engine()
            logger.info("OCR 引擎预热完成")
        except Exception as error:
            logger.warning("OCR 预热跳过（模型不可用）: %s", str(error)[:100])

    asyncio.create_task(warm_ocr())

    async def warm_matching() -> None:
        # 岗位画像共享缓存预热：首次 ARQ 匹配免冷加载（Redis 单飞构建载荷并切指针；
        # 失败不阻塞 worker 启动，匹配请求走降级路径）
        try:
            from app.services.matching.shared_cache import load_positions_shared

            await load_positions_shared()
            logger.info("岗位画像共享缓存预热完成")
        except Exception as error:
            logger.warning("岗位画像预热跳过: %s", str(error)[:100])

    asyncio.create_task(warm_matching())


async def on_shutdown(ctx: dict) -> None:
    """Log worker shutdown."""
    logger.info("ARQ worker 关闭")


def _crawler_cron_jobs() -> list:
    """每爬虫独立触发时间的 cron 注册（08-21b）。

    arq cron 无法给任务传参，故注册**单个每分钟 cron** 调用 crawl_scheduler；
    crawl_scheduler 内部按"当前 HH:MM == 配置 hour/minute"匹配到点的爬虫并触发。
    未配置独立时间的爬虫由 ETL 主管线统一触发（crawl_scheduler 内跳过，防双跑）。
    worker 重启后生效（与其余运行时配置一致）。
    """
    return [
        cron(
            crawl_scheduler,
            minute=set(range(0, 60)),
            run_at_startup=False,
        )
    ]


class WorkerSettings:
    """ARQ worker configuration.

    Start with ``arq app.workers.settings.WorkerSettings``.
    """

    functions = [
        # crawl_platform 显式放宽超时（H2 修复）：全局 job_timeout=1800s 会
        # 在 zhilian 独立触发合法采集（最长 7200s）完成前 kill 掉 ARQ 任务。
        # 7200s 对齐 _CRAWL_TIMEOUT_BY_SPIDER["zhilian"]；其余源内部 900s 兜底。
        func(crawl_platform, timeout=7200, max_tries=1),
        func(crawl_scheduler, timeout=7200, max_tries=1),
        func(run_etl_pipeline, timeout=10800, max_tries=1),
        func(run_etl_pipeline_scheduled, timeout=10800, max_tries=1),
        dedup_simhash,
        validate_temporal,
        detect_inflation,
        resume_parse,
        match_recommend,
        generate_diagnosis,
        batch_extract,
        enrich_course_skills,
        load_courses,
        evaluate_courses,
        diversity_report,
        check_data_freshness,
        aggregate_positions,
        cross_validate_jds,
        sync_skill_normalization,
        backfill_embeddings,
        discovery_daily,
        discovery_auto_transition,
        watch_signal_daily,
        snapshot_graph,
        dict_guard_daily,
        check_llm_providers_health,
        graph_health_check,
    ]
    on_startup = on_startup
    on_shutdown = on_shutdown
    redis_settings = RedisSettings.from_dsn(settings.arq_redis_url)
    concurrency = runtime_config.get("arq_concurrency", settings.arq_concurrency)
    job_timeout = runtime_config.get("arq_job_timeout", settings.arq_job_timeout)
    max_retries = 2
    retry_delay = 10
    cron_jobs = [
        cron(
            check_llm_providers_health,
            minute=set(range(0, 60, 5)),
            run_at_startup=True,
        ),
        cron(watch_signal_daily, hour=6, minute=0),
        # ETL 主管线（08-21 容器内调度，替代外部计划任务 etl_daily.py）：
        # 执行时间来自配置中心 runtime_config.etl_run_hour/minute，重启后生效；
        # 当日幂等由 run_etl_pipeline_scheduled 内部 Redis 锁保证。
        cron(
            run_etl_pipeline_scheduled,
            hour=runtime_config.get("etl_run_hour", 5),
            minute=runtime_config.get("etl_run_minute", 0),
            run_at_startup=False,
        ),
        # 每爬虫独立 cron（08-21b）：仅配置了 hour+minute 的启用爬虫注册；
        # 未配置独立时间的爬虫由 ETL 主管线统一触发（防双跑）。
        *_crawler_cron_jobs(),
    ]
