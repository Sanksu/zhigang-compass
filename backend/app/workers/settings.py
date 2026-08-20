"""ARQ worker registration and lifecycle settings."""

import asyncio

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
    cross_validate_jds,
    dedup_simhash,
    detect_inflation,
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


async def on_startup(ctx: dict) -> None:
    """Preload the OCR engine without blocking worker startup."""
    print(f"[ARQ Worker] 启动，PID={ctx.get('worker_pid')}")

    async def warm_ocr() -> None:
        try:
            from app.services.resume import file_parser

            file_parser._ocr_engine()
            print("[ARQ Worker] OCR 引擎预热完成")
        except Exception as error:
            print(f"[ARQ Worker] OCR 预热跳过（模型不可用）: {str(error)[:100]}")

    asyncio.create_task(warm_ocr())

    async def warm_matching() -> None:
        # 岗位画像共享缓存预热：首次 ARQ 匹配免冷加载（Redis 单飞构建载荷并切指针；
        # 失败不阻塞 worker 启动，匹配请求走降级路径）
        try:
            from app.services.matching.shared_cache import load_positions_shared

            await load_positions_shared()
            print("[ARQ Worker] 岗位画像共享缓存预热完成")
        except Exception as error:
            print(f"[ARQ Worker] 岗位画像预热跳过: {str(error)[:100]}")

    asyncio.create_task(warm_matching())


async def on_shutdown(ctx: dict) -> None:
    """Log worker shutdown."""
    print("[ARQ Worker] 关闭")


class WorkerSettings:
    """ARQ worker configuration.

    Start with ``arq app.workers.settings.WorkerSettings``.
    """

    functions = [
        crawl_platform,
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
    ]
